"""Form reliability watchdog for enhanced form handling."""

import asyncio
import logging
from typing import TYPE_CHECKING, ClassVar

from bubus import BaseEvent

from browser_use.browser.events import (
	BrowserErrorEvent,
	ClickElementEvent,
	SendKeysEvent,
	TypeTextEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog

if TYPE_CHECKING:
	pass

logger = logging.getLogger(__name__)


class FormReliabilityWatchdog(BaseWatchdog):
	"""Enhanced form submission and input reliability."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		ClickElementEvent,
		TypeTextEvent,
		SendKeysEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		BrowserErrorEvent,
	]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._form_submission_tracking = {}

	async def on_ClickElementEvent(self, event: ClickElementEvent) -> None:
		"""Monitor clicks on form submission elements."""
		element = event.node
		if not element:
			return

		# Check if this is a form submission element
		tag_name = element.tag_name.lower() if element.tag_name else ''
		element_type = element.attributes.get('type', '').lower() if element.attributes else ''
		element_role = element.attributes.get('role', '').lower() if element.attributes else ''

		is_submit_element = (
			(tag_name == 'button' and element_type in ['submit', '']) or
			(tag_name == 'input' and element_type == 'submit') or
			element_role == 'button' or
			self._is_likely_submit_button(element)
		)

		if is_submit_element:
			await self._track_form_submission(event.node)

	async def on_TypeTextEvent(self, event: TypeTextEvent) -> None:
		"""Enhanced text input with validation checking."""
		if not event.node:
			return

		# Wait a moment for the input to be processed
		await asyncio.sleep(0.1)

		# Verify the input was successful
		await self._verify_input_success(event.node, event.text)

	async def on_SendKeysEvent(self, event: SendKeysEvent) -> None:
		"""Monitor key events for form interactions."""
		if event.keys == 'Enter':
			# Enter key might trigger form submission
			await self._detect_form_submission_by_enter()

	def _is_likely_submit_button(self, element) -> bool:
		"""Determine if an element is likely a submit button based on text content."""
		text_content = element.get_all_children_text(max_depth=2).lower()
		submit_keywords = [
			'submit', 'send', 'save', 'continue', 'proceed', 'next',
			'login', 'sign in', 'register', 'sign up', 'search',
			'go', 'apply', 'confirm', 'create', 'add', 'update'
		]
		return any(keyword in text_content for keyword in submit_keywords)

	async def _track_form_submission(self, element) -> None:
		"""Track form submission and verify success."""
		try:
			if not self.browser_session.agent_focus:
				return

			session = await self.browser_session.get_or_create_cdp_session(
				self.browser_session.agent_focus.target_id
			)

			# Capture current page state before submission
			pre_submit_state = await self._capture_page_state(session)

			element_info = {
				'tag_name': element.tag_name,
				'text': element.get_all_children_text(max_depth=2),
				'index': element.element_index
			}

			self.logger.debug(f"📝 Tracking form submission for element: {element_info}")

			# Wait for potential form submission processing
			await asyncio.sleep(1.0)

			# Check for form submission indicators
			await self._verify_form_submission_success(session, pre_submit_state, element_info)

		except Exception as e:
			self.logger.debug(f"Form submission tracking failed: {e}")

	async def _capture_page_state(self, session) -> dict:
		"""Capture current page state for comparison."""
		try:
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						return {
							url: window.location.href,
							title: document.title,
							hasErrorMessages: !!document.querySelector('.error, .alert, .warning, [class*="error" i], [id*="error" i]'),
							hasSuccessMessages: !!document.querySelector('.success, .confirmation, [class*="success" i], [id*="success" i]'),
							hasLoadingIndicators: !!document.querySelector('[class*="loading" i], [class*="spinner" i], [class*="processing" i]'),
							formCount: document.forms.length,
							readyState: document.readyState
						};
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)
			return result.get('result', {}).get('value', {})
		except Exception:
			return {}

	async def _verify_form_submission_success(self, session, pre_submit_state: dict, element_info: dict) -> None:
		"""Verify that form submission was successful."""
		try:
			# Wait for potential page changes
			max_wait_time = 10.0
			check_interval = 0.5
			elapsed = 0.0

			while elapsed < max_wait_time:
				current_state = await self._capture_page_state(session)

				# Check for URL change (common indicator of successful submission)
				if current_state.get('url') != pre_submit_state.get('url'):
					self.logger.info(f"✅ Form submission successful - URL changed to: {current_state.get('url')}")
					return

				# Check for success messages
				if current_state.get('hasSuccessMessages', False):
					self.logger.info("✅ Form submission successful - success message detected")
					return

				# Check for error messages
				if current_state.get('hasErrorMessages', False):
					await self._handle_form_submission_error(session, element_info)
					return

				# Check if still loading
				if current_state.get('hasLoadingIndicators', False):
					self.logger.debug("⏳ Form still processing...")
					await asyncio.sleep(check_interval * 2)  # Wait longer for processing
					elapsed += check_interval * 2
					continue

				await asyncio.sleep(check_interval)
				elapsed += check_interval

			# Timeout reached, check final state
			final_state = await self._capture_page_state(session)
			if final_state.get('url') == pre_submit_state.get('url') and not final_state.get('hasSuccessMessages', False):
				self.logger.warning(f"⚠️ Form submission unclear - no clear success/failure indicators detected for element {element_info}")

		except Exception as e:
			self.logger.debug(f"Form submission verification failed: {e}")

	async def _handle_form_submission_error(self, session, element_info: dict) -> None:
		"""Handle form submission errors by extracting error messages."""
		try:
			error_result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						const errorElements = document.querySelectorAll('.error, .alert, .warning, [class*="error" i], [id*="error" i]');
						const errors = Array.from(errorElements).map(el => ({
							text: el.textContent.trim(),
							visible: window.getComputedStyle(el).display !== 'none'
						})).filter(err => err.visible && err.text);

						return {
							errorCount: errors.length,
							errorMessages: errors.slice(0, 3).map(e => e.text) // Limit to first 3 errors
						};
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			error_data = error_result.get('result', {}).get('value', {})
			error_messages = error_data.get('errorMessages', [])

			if error_messages:
				error_text = '; '.join(error_messages)
				self.logger.warning(f"❌ Form submission failed for element {element_info}: {error_text}")
			else:
				self.logger.warning(f"❌ Form submission failed for element {element_info}: Unknown error")

		except Exception as e:
			self.logger.debug(f"Error message extraction failed: {e}")

	async def _verify_input_success(self, element, expected_text: str) -> None:
		"""Verify that text input was successful."""
		try:
			if not self.browser_session.agent_focus:
				return

			session = await self.browser_session.get_or_create_cdp_session(
				self.browser_session.agent_focus.target_id
			)

			# Use the element's backend node ID to verify input
			if not element.backend_node_id:
				return

			# Get the current value of the input element
			value_result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': f'''
					(() => {{
						const element = document.querySelector('[data-backend-node-id="{element.backend_node_id}"]');
						if (!element) return null;
						return {{
							value: element.value || element.textContent,
							focused: document.activeElement === element,
							disabled: element.disabled,
							readonly: element.readOnly
						}};
					}})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			input_data = value_result.get('result', {}).get('value')
			if not input_data:
				return

			current_value = input_data.get('value', '')

			# Check if the input was successful
			if expected_text in current_value or current_value in expected_text:
				self.logger.debug(f"✅ Input verification successful for element {element.element_index}")
			else:
				self.logger.warning(f"⚠️ Input verification failed - expected '{expected_text}', got '{current_value}' for element {element.element_index}")

				# Check for input issues
				if input_data.get('disabled'):
					self.logger.warning("⚠️ Input element is disabled")
				elif input_data.get('readonly'):
					self.logger.warning("⚠️ Input element is readonly")
				elif not input_data.get('focused'):
					self.logger.debug("ℹ️ Input element lost focus during typing")

		except Exception as e:
			self.logger.debug(f"Input verification failed: {e}")

	async def _detect_form_submission_by_enter(self) -> None:
		"""Detect potential form submission triggered by Enter key."""
		try:
			if not self.browser_session.agent_focus:
				return

			session = await self.browser_session.get_or_create_cdp_session(
				self.browser_session.agent_focus.target_id
			)

			# Check if we're in a form context
			form_result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						const activeElement = document.activeElement;
						if (!activeElement) return null;

						// Check if active element is in a form
						const form = activeElement.closest('form');
						if (form) {
							return {
								inForm: true,
								formAction: form.action,
								formMethod: form.method,
								elementType: activeElement.type || activeElement.tagName.toLowerCase()
							};
						}
						return { inForm: false };
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			form_data = form_result.get('result', {}).get('value')
			if form_data and form_data.get('inForm'):
				self.logger.debug(f"📝 Enter key pressed in form context: {form_data}")

				# Wait a moment to see if form submission occurs
				await asyncio.sleep(0.5)

				# Could implement additional form submission tracking here

		except Exception as e:
			self.logger.debug(f"Form context detection failed: {e}")