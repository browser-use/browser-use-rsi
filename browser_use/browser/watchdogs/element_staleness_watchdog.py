"""Element staleness detection and recovery watchdog."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, ClassVar, Any

from bubus import BaseEvent

from browser_use.browser.events import (
	BrowserErrorEvent,
	ClickElementEvent,
	TypeTextEvent,
	UploadFileEvent,
	ScrollEvent,
	BrowserStateRequestEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.dom.views import EnhancedDOMTreeNode

if TYPE_CHECKING:
	pass

logger = logging.getLogger(__name__)


class ElementStalenessWatchdog(BaseWatchdog):
	"""Detects and recovers from element staleness issues with intelligent retry logic."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		ClickElementEvent,
		TypeTextEvent,
		UploadFileEvent,
		ScrollEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		BrowserErrorEvent,
		BrowserStateRequestEvent,
	]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._element_staleness_cache = {}  # Track element staleness detection
		self._retry_counts = {}  # Track retry attempts per element
		self._last_dom_rebuild = 0  # Track when we last rebuilt DOM

	async def on_ClickElementEvent(self, event: ClickElementEvent) -> dict[str, Any] | None:
		"""Handle click element events with staleness detection."""
		return await self._handle_element_action_with_staleness_check(
			'click', event.node, event
		)

	async def on_TypeTextEvent(self, event: TypeTextEvent) -> dict | None:
		"""Handle type text events with staleness detection."""
		return await self._handle_element_action_with_staleness_check(
			'type', event.node, event
		)

	async def on_UploadFileEvent(self, event: UploadFileEvent) -> None:
		"""Handle upload file events with staleness detection."""
		await self._handle_element_action_with_staleness_check(
			'upload', event.node, event
		)
		return None

	async def on_ScrollEvent(self, event: ScrollEvent) -> None:
		"""Handle scroll events with staleness detection for elements."""
		if event.node is not None:  # Only check staleness for element-based scrolling
			await self._handle_element_action_with_staleness_check(
				'scroll', event.node, event
			)
		return None

	async def _handle_element_action_with_staleness_check(
		self,
		action_type: str,
		node: EnhancedDOMTreeNode,
		original_event: BaseEvent,
	) -> Any:
		"""Handle element actions with staleness detection and recovery."""
		element_key = self._get_element_key(node)
		retry_key = f"{action_type}_{element_key}"

		try:
			# Check if element appears to be stale
			is_stale = await self._check_element_staleness(node)

			if is_stale:
				self.logger.warning(f"🔄 Element staleness detected for {action_type} action on element {node.element_index}")
				return await self._handle_stale_element(action_type, node, original_event, retry_key)

			# Element appears fresh, continue with normal processing
			self._reset_retry_count(retry_key)
			return None  # Let the original handler proceed

		except Exception as e:
			self.logger.error(f"❌ Element staleness check failed for {action_type}: {e}")
			# Don't block the action, let it proceed
			return None

	async def _check_element_staleness(self, node: EnhancedDOMTreeNode) -> bool:
		"""Check if an element appears to be stale."""
		try:
			# Get current session for the element's target
			if not node.target_id:
				return False  # Can't check without target_id

			session = await self.browser_session.get_or_create_cdp_session(
				target_id=node.target_id
			)

			# Check if the element still exists in the DOM
			try:
				# Try to describe the node to see if it's still valid
				describe_result = await session.cdp_client.send.DOM.describeNode(
					params={'nodeId': node.node_id},
					session_id=session.session_id
				)

				# If we get here without exception, the element exists
				node_description = describe_result.get('node', {})

				# Additional staleness indicators:
				# 1. Node name changed unexpectedly
				# 2. Node has no attributes when it should have some
				# 3. Node position appears invalid

				original_name = node.node_name or ''
				current_name = node_description.get('nodeName', '').lower()

				if original_name and current_name and original_name.lower() != current_name:
					self.logger.debug(f"Node name changed: {original_name} -> {current_name}")
					return True

				return False  # Element appears to be valid

			except Exception as e:
				error_str = str(e).lower()
				# Common CDP errors indicating stale elements
				stale_indicators = [
					'node not found',
					'could not find node',
					'invalid node id',
					'node is not attached',
					'disconnected frame',
					'execution context destroyed'
				]

				if any(indicator in error_str for indicator in stale_indicators):
					self.logger.debug(f"Staleness detected via CDP error: {error_str}")
					return True

				# Unknown error - assume not stale to avoid false positives
				self.logger.debug(f"Unknown error in staleness check: {e}")
				return False

		except Exception as e:
			self.logger.debug(f"Staleness check failed: {e}")
			return False  # Assume not stale if we can't check

	async def _handle_stale_element(
		self,
		action_type: str,
		stale_node: EnhancedDOMTreeNode,
		original_event: BaseEvent,
		retry_key: str,
	) -> Any:
		"""Handle stale element by rebuilding DOM and finding fresh element."""
		retry_count = self._get_retry_count(retry_key)
		max_retries = 2  # Allow up to 2 retries for stale elements

		if retry_count >= max_retries:
			error_msg = f"Element staleness retry limit exceeded for {action_type} on element {stale_node.element_index}"
			self.logger.error(f"❌ {error_msg}")

			# Emit browser error event to inform other components
			self.event_bus.dispatch(BrowserErrorEvent(
				error_type='ElementStalenessRetryExceeded',
				message=error_msg,
				details={
					'action_type': action_type,
					'element_index': stale_node.element_index,
					'retry_count': retry_count
				}
			))
			return None  # Let original action fail normally

		self.logger.info(f"🔄 Attempting to recover from stale element (attempt #{retry_count + 1})")

		try:
			# Increment retry count
			self._increment_retry_count(retry_key)

			# Force DOM rebuild to get fresh element references
			await self._force_dom_rebuild()

			# Try to find the element again by its properties
			fresh_element = await self._find_fresh_element_equivalent(stale_node)

			if fresh_element:
				self.logger.info(f"✅ Found fresh equivalent element for {action_type}")

				# Update the original event with the fresh element
				await self._update_event_with_fresh_element(original_event, fresh_element)

				# Reset retry count on successful recovery
				self._reset_retry_count(retry_key)

				return None  # Let the action proceed with fresh element
			else:
				self.logger.warning(f"❌ Could not find fresh equivalent element for {action_type}")
				return None  # Let the action fail normally

		except Exception as e:
			self.logger.error(f"❌ Stale element recovery failed: {e}")
			return None

	async def _force_dom_rebuild(self) -> None:
		"""Force a DOM rebuild by dispatching a browser state request."""
		current_time = time.time()

		# Avoid rebuilding DOM too frequently (minimum 1 second between rebuilds)
		if current_time - self._last_dom_rebuild < 1.0:
			self.logger.debug("Skipping DOM rebuild - too recent")
			return

		self.logger.debug("🔧 Forcing DOM rebuild due to staleness")

		try:
			# Clear DOM cache in DOM watchdog
			dom_watchdog = self._get_dom_watchdog()
			if dom_watchdog:
				dom_watchdog.clear_cache()

			# Request fresh browser state to rebuild DOM
			state_request = self.event_bus.dispatch(
				BrowserStateRequestEvent(
					include_dom=True,
					include_screenshot=False,  # Skip screenshot for performance
					cache_clickable_elements_hashes=True
				)
			)

			await state_request
			await state_request.event_result(raise_if_any=False, raise_if_none=False)

			self._last_dom_rebuild = current_time
			self.logger.debug("✅ DOM rebuild completed")

		except Exception as e:
			self.logger.error(f"❌ DOM rebuild failed: {e}")

	async def _find_fresh_element_equivalent(self, stale_node: EnhancedDOMTreeNode) -> EnhancedDOMTreeNode | None:
		"""Find a fresh element equivalent to the stale one."""
		try:
			# Get the DOM watchdog to access fresh selector map
			dom_watchdog = self._get_dom_watchdog()
			if not dom_watchdog or not dom_watchdog.selector_map:
				return None

			# Try to find by element index first (most direct match)
			fresh_element = dom_watchdog.selector_map.get(stale_node.element_index)
			if fresh_element:
				return fresh_element

			# If exact index not found, try to find by element characteristics
			return await self._find_by_element_characteristics(stale_node, dom_watchdog.selector_map)

		except Exception as e:
			self.logger.debug(f"Fresh element lookup failed: {e}")
			return None

	async def _find_by_element_characteristics(
		self,
		stale_node: EnhancedDOMTreeNode,
		fresh_selector_map: dict[int, EnhancedDOMTreeNode]
	) -> EnhancedDOMTreeNode | None:
		"""Find element by matching characteristics."""
		# Look for elements with matching node name, attributes, and position
		best_match = None
		best_match_score = 0

		for fresh_node in fresh_selector_map.values():
			score = 0

			# Match node name
			if (stale_node.node_name and fresh_node.node_name and
				stale_node.node_name.lower() == fresh_node.node_name.lower()):
				score += 10

			# Match attributes
			if stale_node.attributes and fresh_node.attributes:
				common_attrs = set(stale_node.attributes.keys()) & set(fresh_node.attributes.keys())
				for attr in common_attrs:
					if stale_node.attributes[attr] == fresh_node.attributes[attr]:
						score += 2

			# Match approximate position (within 50px tolerance)
			if (stale_node.absolute_position and fresh_node.absolute_position):
				x_diff = abs(stale_node.absolute_position.x - fresh_node.absolute_position.x)
				y_diff = abs(stale_node.absolute_position.y - fresh_node.absolute_position.y)
				if x_diff < 50 and y_diff < 50:
					score += 5

			# Update best match if this is better
			if score > best_match_score and score >= 15:  # Minimum threshold
				best_match_score = score
				best_match = fresh_node

		if best_match:
			self.logger.debug(f"Found element match with score {best_match_score}")

		return best_match

	async def _update_event_with_fresh_element(
		self,
		event: BaseEvent,
		fresh_element: EnhancedDOMTreeNode
	) -> None:
		"""Update the event object with fresh element reference."""
		if hasattr(event, 'node'):
			event.node = fresh_element
			self.logger.debug(f"Updated event with fresh element index {fresh_element.element_index}")

	def _get_dom_watchdog(self):
		"""Get the DOM watchdog instance from the browser session."""
		try:
			# Access the DOM watchdog instance directly from browser session
			return getattr(self.browser_session, '_dom_watchdog', None)
		except Exception:
			return None

	def _get_element_key(self, node: EnhancedDOMTreeNode) -> str:
		"""Generate a key for element identification."""
		return f"{node.target_id}_{node.element_index}_{node.node_id}"

	def _get_retry_count(self, retry_key: str) -> int:
		"""Get current retry count for a key."""
		return self._retry_counts.get(retry_key, 0)

	def _increment_retry_count(self, retry_key: str) -> None:
		"""Increment retry count for a key."""
		self._retry_counts[retry_key] = self._get_retry_count(retry_key) + 1

	def _reset_retry_count(self, retry_key: str) -> None:
		"""Reset retry count for a key."""
		self._retry_counts.pop(retry_key, None)