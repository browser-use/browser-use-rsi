"""Enhanced navigation watchdog with anti-bot detection and robust page loading."""

import asyncio
import logging
import random
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


class EnhancedNavigationWatchdog(BaseWatchdog):
	"""Enhanced navigation watchdog with anti-bot detection and robust page loading strategies."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		NavigationCompleteEvent,
		NavigationStartedEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		BrowserErrorEvent,
	]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._navigation_attempts = {}
		self._loading_retry_count = {}

	async def on_NavigationStartedEvent(self, event: NavigationStartedEvent) -> None:
		"""Handle navigation start - add human-like delays and prepare for anti-bot detection."""
		# Add small random delay to appear more human
		delay = random.uniform(0.3, 1.2)
		await asyncio.sleep(delay)

		# Reset retry counters for this URL
		self._navigation_attempts[event.url] = self._navigation_attempts.get(event.url, 0) + 1
		self._loading_retry_count[event.url] = 0

		self.logger.debug(f"🚀 Enhanced navigation started for {event.url} (attempt #{self._navigation_attempts[event.url]})")

	async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
		"""Enhanced navigation completion with anti-bot detection and page load verification."""
		try:
			# Basic error handling first
			if event.error_message:
				self.logger.warning(f"❌ Navigation completed with error: {event.error_message}")
				return

			# Wait for initial page load
			await asyncio.sleep(1.0)

			# Enhanced page load verification
			await self._verify_page_loaded(event.target_id, event.url)

			# Anti-bot detection and handling
			await self._detect_and_handle_antibot(event.target_id, event.url)

			# Final page content verification
			await self._verify_content_loaded(event.target_id, event.url)

		except Exception as e:
			self.logger.error(f"❌ Enhanced navigation failed for {event.url}: {e}")
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='EnhancedNavigationFailed',
					message=f'Enhanced navigation processing failed: {str(e)}',
					details={'url': event.url, 'target_id': event.target_id},
				)
			)

	async def _verify_page_loaded(self, target_id: str, url: str) -> None:
		"""Verify the page has fully loaded by checking for loading indicators."""
		max_wait_time = 30.0
		start_time = asyncio.get_event_loop().time()

		while (asyncio.get_event_loop().time() - start_time) < max_wait_time:
			try:
				# Get current page state
				session = await self.browser_session.get_or_create_cdp_session(target_id)

				# Check for loading indicators
				result = await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							// Check for common loading indicators
							const loadingElements = document.querySelectorAll('[class*="loading" i], [id*="loading" i], [class*="spinner" i], [class*="loader" i]');
							const loadingTexts = Array.from(document.querySelectorAll('*')).filter(el =>
								el.textContent && (
									el.textContent.toLowerCase().includes('loading') ||
									el.textContent.toLowerCase().includes('please wait') ||
									el.textContent.toLowerCase().includes('processing')
								)
							);

							// Check document ready state
							const isDocumentReady = document.readyState === 'complete';

							// Check for visible loading indicators
							const hasVisibleLoading = Array.from(loadingElements).some(el => {
								const style = window.getComputedStyle(el);
								return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
							}) || loadingTexts.some(el => {
								const style = window.getComputedStyle(el);
								return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
							});

							return {
								isDocumentReady,
								hasVisibleLoading,
								loadingElementCount: loadingElements.length,
								loadingTextCount: loadingTexts.length,
								readyState: document.readyState
							};
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)

				page_state = result.get('result', {}).get('value', {})

				# If page is ready and no loading indicators, we're good
				if page_state.get('isDocumentReady', False) and not page_state.get('hasVisibleLoading', True):
					self.logger.debug(f"✅ Page fully loaded: {url}")
					return

				# If still loading, wait and check again
				if page_state.get('hasVisibleLoading', False):
					self.logger.debug(f"⏳ Page still loading ({page_state.get('readyState', 'unknown')}): {url}")
					await asyncio.sleep(2.0)
					continue

				# If document is ready but we detected loading elements, wait a bit more
				if page_state.get('isDocumentReady', False):
					self.logger.debug(f"📄 Document ready but checking for dynamic content: {url}")
					await asyncio.sleep(1.0)
					return

			except Exception as e:
				self.logger.debug(f"Page load check failed: {e}")

			await asyncio.sleep(1.0)

		self.logger.warning(f"⚠️ Page load verification timeout for {url}")

	async def _detect_and_handle_antibot(self, target_id: str, url: str) -> None:
		"""Detect and handle anti-bot challenges like Cloudflare."""
		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id)

			# Check for anti-bot challenges
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						// Check for Cloudflare challenges
						const cloudflareSelectors = [
							'[data-ray]',
							'.cf-browser-verification',
							'.cf-challenge-form',
							'#cf-challenge-stage',
							'.challenge-form'
						];

						// Check for other anti-bot challenges
						const antibotSelectors = [
							'[class*="captcha" i]',
							'[id*="captcha" i]',
							'[class*="verification" i]',
							'[class*="challenge" i]',
							'[class*="security" i]'
						];

						const allSelectors = [...cloudflareSelectors, ...antibotSelectors];
						let challengeFound = false;
						let challengeType = '';

						for (const selector of allSelectors) {
							const element = document.querySelector(selector);
							if (element) {
								const style = window.getComputedStyle(element);
								if (style.display !== 'none' && style.visibility !== 'hidden') {
									challengeFound = true;
									challengeType = selector;
									break;
								}
							}
						}

						// Check for challenge text content
						const pageText = document.body.textContent.toLowerCase();
						const challengeTexts = [
							'cloudflare',
							'checking your browser',
							'human verification',
							'please wait while we verify',
							'security check',
							'press & hold',
							'click to verify',
							'i am human'
						];

						let textChallenge = '';
						for (const text of challengeTexts) {
							if (pageText.includes(text)) {
								challengeFound = true;
								textChallenge = text;
								break;
							}
						}

						return {
							challengeFound,
							challengeType,
							textChallenge,
							pageTitle: document.title,
							hasCheckbox: !!document.querySelector('input[type="checkbox"]'),
							hasButton: !!document.querySelector('button, input[type="button"], input[type="submit"]')
						};
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			challenge_data = result.get('result', {}).get('value', {})

			if challenge_data.get('challengeFound', False):
				await self._handle_antibot_challenge(target_id, url, challenge_data)

		except Exception as e:
			self.logger.debug(f"Anti-bot detection failed: {e}")

	async def _handle_antibot_challenge(self, target_id: str, url: str, challenge_data: dict) -> None:
		"""Handle detected anti-bot challenge."""
		self.logger.warning(f"🤖 Anti-bot challenge detected on {url}: {challenge_data}")

		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id)

			# Try basic challenge handling
			if challenge_data.get('hasCheckbox', False):
				# Try clicking verification checkbox
				await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							const checkbox = document.querySelector('input[type="checkbox"]');
							if (checkbox) {
								checkbox.click();
								return true;
							}
							return false;
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)
				await asyncio.sleep(2.0)

			if challenge_data.get('hasButton', False):
				# Try clicking verification button
				await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							const buttons = document.querySelectorAll('button, input[type="button"], input[type="submit"]');
							for (const button of buttons) {
								const text = button.textContent.toLowerCase();
								if (text.includes('verify') || text.includes('continue') || text.includes('proceed')) {
									button.click();
									return true;
								}
							}
							return false;
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)
				await asyncio.sleep(3.0)

			# Wait for challenge resolution
			await self._wait_for_challenge_resolution(target_id, url)

		except Exception as e:
			self.logger.error(f"❌ Failed to handle anti-bot challenge: {e}")
			# Don't raise - let the browser continue with limited functionality

	async def _wait_for_challenge_resolution(self, target_id: str, url: str) -> None:
		"""Wait for anti-bot challenge to be resolved."""
		max_wait_time = 15.0
		start_time = asyncio.get_event_loop().time()

		while (asyncio.get_event_loop().time() - start_time) < max_wait_time:
			try:
				session = await self.browser_session.get_or_create_cdp_session(target_id)

				# Check if challenge is still present
				result = await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							const pageText = document.body.textContent.toLowerCase();
							const challengeTexts = ['cloudflare', 'checking your browser', 'human verification'];
							return challengeTexts.some(text => pageText.includes(text));
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)

				still_has_challenge = result.get('result', {}).get('value', True)

				if not still_has_challenge:
					self.logger.info(f"✅ Anti-bot challenge resolved for {url}")
					return

				await asyncio.sleep(1.0)

			except Exception as e:
				self.logger.debug(f"Challenge resolution check failed: {e}")
				await asyncio.sleep(1.0)

		self.logger.warning(f"⏰ Anti-bot challenge resolution timeout for {url}")

	async def _verify_content_loaded(self, target_id: str, url: str) -> None:
		"""Verify that meaningful content has loaded on the page."""
		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id)

			# Wait a moment for dynamic content
			await asyncio.sleep(1.0)

			# Check for meaningful content
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						// Check for meaningful content indicators
						const contentSelectors = [
							'main', 'article', '.content', '#content',
							'.main', '.main-content', '.page-content',
							'form', 'input', 'button', 'table', 'ul', 'ol'
						];

						let contentFound = false;
						let contentElements = 0;

						for (const selector of contentSelectors) {
							const elements = document.querySelectorAll(selector);
							if (elements.length > 0) {
								contentFound = true;
								contentElements += elements.length;
							}
						}

						// Also check for text content
						const textLength = document.body.textContent.trim().length;

						return {
							contentFound,
							contentElements,
							textLength,
							hasImages: document.images.length > 0,
							hasLinks: document.links.length > 0
						};
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			content_data = result.get('result', {}).get('value', {})

			if (content_data.get('contentFound', False) or
				content_data.get('textLength', 0) > 100 or
				content_data.get('hasLinks', False)):
				self.logger.debug(f"✅ Content verified for {url}: {content_data}")
			else:
				self.logger.warning(f"⚠️ Limited content detected on {url}: {content_data}")
				# Consider page reload if content is insufficient
				await self._consider_page_reload(target_id, url)

		except Exception as e:
			self.logger.debug(f"Content verification failed: {e}")

	async def _consider_page_reload(self, target_id: str, url: str) -> None:
		"""Consider reloading the page if content seems insufficient."""
		retry_count = self._loading_retry_count.get(url, 0)

		if retry_count < 2:  # Allow up to 2 retries
			self._loading_retry_count[url] = retry_count + 1
			self.logger.info(f"🔄 Reloading page due to insufficient content (retry #{retry_count + 1}): {url}")

			try:
				session = await self.browser_session.get_or_create_cdp_session(target_id)
				await session.cdp_client.send.Page.reload(session_id=session.session_id)

				# Wait for reload
				await asyncio.sleep(3.0)

			except Exception as e:
				self.logger.error(f"❌ Page reload failed: {e}")