"""Network reliability watchdog for handling network errors and retries."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, ClassVar

from bubus import BaseEvent

from browser_use.browser.events import (
	BrowserErrorEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	NavigationStartedEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog

if TYPE_CHECKING:
	pass

logger = logging.getLogger(__name__)


class NetworkReliabilityWatchdog(BaseWatchdog):
	"""Enhanced network reliability with intelligent retry mechanisms."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		NavigationStartedEvent,
		NavigationCompleteEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		BrowserErrorEvent,
		NavigateToUrlEvent,
	]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._navigation_attempts = {}
		self._network_failure_count = {}
		self._last_network_failure = {}

	async def on_NavigationStartedEvent(self, event: NavigationStartedEvent) -> None:
		"""Track navigation starts and reset network failure counts periodically."""
		# Reset old failure tracking (older than 5 minutes)
		current_time = time.time()
		for url in list(self._last_network_failure.keys()):
			if current_time - self._last_network_failure[url] > 300:  # 5 minutes
				self._network_failure_count.pop(url, None)
				self._last_network_failure.pop(url, None)

		self.logger.debug(f"🌐 Network reliability tracking started for {event.url}")

	async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
		"""Handle navigation completion with network error detection and retry logic."""
		if not event.error_message:
			# Successful navigation, reset failure count
			self._network_failure_count.pop(event.url, None)
			self._last_network_failure.pop(event.url, None)
			return

		# Check if this is a network-related error
		error_lower = event.error_message.lower()
		network_keywords = [
			'net::err_',
			'dns_probe_finished',
			'connection_refused',
			'connection_timed_out',
			'network_changed',
			'internet_disconnected',
			'name_not_resolved',
			'connection_reset',
			'ssl_protocol_error',
			'cert_',
			'timeout',
			'failed to load',
			'server not found',
		]

		is_network_error = any(keyword in error_lower for keyword in network_keywords)

		if is_network_error:
			await self._handle_network_error(event.url, event.error_message, event.target_id)

	async def _handle_network_error(self, url: str, error_message: str, target_id: str) -> None:
		"""Handle network errors with intelligent retry logic."""
		current_time = time.time()

		# Track failure count and timing
		failure_count = self._network_failure_count.get(url, 0) + 1
		self._network_failure_count[url] = failure_count
		self._last_network_failure[url] = current_time

		self.logger.warning(f"🌐❌ Network error detected for {url} (attempt #{failure_count}): {error_message}")

		# Determine retry strategy based on failure count and error type
		should_retry, retry_delay = self._calculate_retry_strategy(url, error_message, failure_count)

		if should_retry:
			self.logger.info(f"🔄 Retrying navigation to {url} in {retry_delay} seconds (attempt #{failure_count + 1})")

			# Wait before retry
			await asyncio.sleep(retry_delay)

			# Attempt to retry navigation
			try:
				# First, try to reload the current page
				session = await self.browser_session.get_or_create_cdp_session(target_id)

				# Check if we're still on the same problematic URL
				current_url_result = await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': 'window.location.href',
						'returnByValue': True,
					},
					session_id=session.session_id
				)

				current_url = current_url_result.get('result', {}).get('value', '')

				if current_url == url or current_url == 'about:blank':
					# Try page reload first
					self.logger.debug(f"🔄 Attempting page reload for {url}")
					await session.cdp_client.send.Page.reload(
						params={'ignoreCache': True},
						session_id=session.session_id
					)
				else:
					# Navigate to the target URL
					self.logger.debug(f"🔄 Attempting navigation retry to {url}")
					retry_event = NavigateToUrlEvent(url=url, target_id=target_id)
					await self.event_bus.dispatch(retry_event)

			except Exception as e:
				self.logger.error(f"❌ Network retry failed for {url}: {e}")
		else:
			self.logger.warning(f"❌ Network error retry limit reached for {url}, giving up")

	def _calculate_retry_strategy(self, url: str, error_message: str, failure_count: int) -> tuple[bool, float]:
		"""Calculate whether to retry and how long to wait."""
		# Maximum retry attempts based on error type
		max_retries = 3

		# Don't retry certain permanent errors
		permanent_errors = [
			'name_not_resolved',
			'dns_probe_finished_nxdomain',
			'cert_authority_invalid',
			'cert_common_name_invalid',
		]

		error_lower = error_message.lower()
		if any(perm_error in error_lower for perm_error in permanent_errors):
			return False, 0.0

		# Don't retry if we've exceeded max attempts
		if failure_count >= max_retries:
			return False, 0.0

		# Calculate exponential backoff with jitter
		base_delay = 2.0
		retry_delay = base_delay * (2 ** (failure_count - 1))  # Exponential backoff
		retry_delay = min(retry_delay, 30.0)  # Cap at 30 seconds

		# Add some jitter to avoid thundering herd
		import random
		retry_delay += random.uniform(0, 1.0)

		return True, retry_delay

	async def _check_connection_health(self, target_id: str) -> bool:
		"""Check if the browser connection is still healthy."""
		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id)

			# Simple connectivity test
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': 'navigator.onLine',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			return result.get('result', {}).get('value', False)
		except Exception:
			return False