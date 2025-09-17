"""Dynamic content detection watchdog for SPA and AJAX handling."""

import asyncio
import logging
from typing import TYPE_CHECKING, ClassVar, Set

from bubus import BaseEvent

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStateRequestEvent,
	NavigationCompleteEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog

if TYPE_CHECKING:
	pass

logger = logging.getLogger(__name__)


class DynamicContentWatchdog(BaseWatchdog):
	"""Enhanced detection and handling of dynamic content loading."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		NavigationCompleteEvent,
		BrowserStateRequestEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		BrowserErrorEvent,
	]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._monitored_requests: Set[str] = set()
		self._content_stabilization_timers = {}

	async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
		"""Monitor navigation completion for dynamic content detection."""
		if event.error_message:
			return  # Skip error cases

		# Start monitoring for dynamic content after navigation
		await self._start_dynamic_content_monitoring(event.target_id, event.url)

	async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> None:
		"""Ensure dynamic content is loaded before state capture."""
		# Check if event has target_id attribute before accessing it
		if hasattr(event, 'target_id') and event.target_id:
			await self._ensure_dynamic_content_loaded(event.target_id)

	async def _start_dynamic_content_monitoring(self, target_id: str, url: str) -> None:
		"""Start monitoring for dynamic content loading."""
		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id)

			# Set up network request monitoring for AJAX/Fetch requests
			await self._setup_network_monitoring(session, url)

			# Wait for initial content stabilization
			await self._wait_for_content_stabilization(session, url)

		except Exception as e:
			self.logger.debug(f"Dynamic content monitoring failed: {e}")

	async def _setup_network_monitoring(self, session, url: str) -> None:
		"""Set up monitoring for network requests that might load dynamic content."""
		try:
			# Enable network domain
			await session.cdp_client.send.Network.enable(session_id=session.session_id)

			self.logger.debug(f"📡 Dynamic content monitoring enabled for {url}")

		except Exception as e:
			self.logger.debug(f"Network monitoring setup failed: {e}")

	async def _wait_for_content_stabilization(self, session, url: str, timeout: float = 15.0) -> None:
		"""Wait for content to stabilize by monitoring DOM changes and network activity."""
		try:
			stabilization_time = 0.0
			check_interval = 0.5
			stable_duration_required = 2.0  # Content must be stable for 2 seconds
			last_content_hash = None
			stable_start_time = None

			self.logger.debug(f"⏳ Waiting for dynamic content stabilization on {url}")

			while stabilization_time < timeout:
				# Check current page content
				content_hash = await self._get_content_hash(session)

				if content_hash == last_content_hash:
					# Content hasn't changed
					if stable_start_time is None:
						stable_start_time = stabilization_time
					elif stabilization_time - stable_start_time >= stable_duration_required:
						self.logger.debug(f"✅ Dynamic content stabilized on {url}")
						break
				else:
					# Content changed, reset stability timer
					stable_start_time = None
					last_content_hash = content_hash
					self.logger.debug(f"📝 Dynamic content change detected on {url}")

				# Check for ongoing network activity
				is_loading = await self._check_loading_activity(session)
				if is_loading:
					stable_start_time = None  # Reset stability if still loading

				await asyncio.sleep(check_interval)
				stabilization_time += check_interval

			if stabilization_time >= timeout:
				self.logger.warning(f"⏰ Dynamic content stabilization timeout for {url}")

		except Exception as e:
			self.logger.debug(f"Content stabilization monitoring failed: {e}")

	async def _get_content_hash(self, session) -> str:
		"""Get a hash of the current page content for change detection."""
		try:
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						// Create a hash of key page elements
						const getTextContent = (selector) => {
							const elements = document.querySelectorAll(selector);
							return Array.from(elements).map(el => el.textContent.trim()).join('|');
						};

						const mainContent = getTextContent('main, article, .content, #content, .main-content');
						const navContent = getTextContent('nav, .navigation, .navbar, .menu');
						const formContent = getTextContent('form, input, button, select');
						const listContent = getTextContent('ul, ol, table');

						// Include counts of key elements
						const elementCounts = {
							images: document.images.length,
							links: document.links.length,
							forms: document.forms.length,
							buttons: document.querySelectorAll('button').length,
							inputs: document.querySelectorAll('input').length
						};

						const contentString = [
							mainContent,
							navContent,
							formContent,
							listContent,
							JSON.stringify(elementCounts),
							document.title
						].join('###');

						// Simple hash function
						let hash = 0;
						for (let i = 0; i < contentString.length; i++) {
							const char = contentString.charCodeAt(i);
							hash = ((hash << 5) - hash) + char;
							hash = hash & hash; // Convert to 32-bit integer
						}
						return hash.toString();
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)
			return result.get('result', {}).get('value', '0')
		except Exception:
			return '0'

	async def _check_loading_activity(self, session) -> bool:
		"""Check if there's ongoing loading activity on the page."""
		try:
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						// Check for loading indicators
						const loadingSelectors = [
							'[class*="loading" i]',
							'[class*="spinner" i]',
							'[class*="loader" i]',
							'[class*="progress" i]',
							'[class*="pending" i]',
							'[id*="loading" i]'
						];

						let hasVisibleLoading = false;
						for (const selector of loadingSelectors) {
							const elements = document.querySelectorAll(selector);
							for (const el of elements) {
								const style = window.getComputedStyle(el);
								if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
									hasVisibleLoading = true;
									break;
								}
							}
							if (hasVisibleLoading) break;
						}

						// Check for AJAX/Fetch activity (if available)
						const hasFetchActivity = typeof window.fetch === 'function' &&
							window.performance &&
							window.performance.getEntriesByType &&
							window.performance.getEntriesByType('navigation').some(entry =>
								entry.loadEventEnd === 0 || entry.domContentLoadedEventEnd === 0
							);

						return {
							hasVisibleLoading,
							hasFetchActivity: hasFetchActivity || false,
							documentReady: document.readyState === 'complete'
						};
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			loading_data = result.get('result', {}).get('value', {})
			return (
				loading_data.get('hasVisibleLoading', False) or
				loading_data.get('hasFetchActivity', False) or
				not loading_data.get('documentReady', True)
			)

		except Exception:
			return False

	async def _ensure_dynamic_content_loaded(self, target_id: str) -> None:
		"""Ensure dynamic content is fully loaded before proceeding."""
		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id)

			# Check for common dynamic content patterns
			await self._wait_for_spa_content(session)
			await self._wait_for_lazy_loaded_images(session)
			await self._wait_for_dynamic_lists(session)

		except Exception as e:
			self.logger.debug(f"Dynamic content verification failed: {e}")

	async def _wait_for_spa_content(self, session, timeout: float = 8.0) -> None:
		"""Wait for Single Page Application content to load."""
		try:
			elapsed = 0.0
			check_interval = 0.5

			self.logger.debug("🔄 Checking for SPA content loading")

			while elapsed < timeout:
				result = await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							// Check for common SPA frameworks
							const hasReact = !!(window.React || document.querySelector('[data-reactroot]'));
							const hasVue = !!(window.Vue || document.querySelector('[data-v-]'));
							const hasAngular = !!(window.angular || window.ng || document.querySelector('[ng-app], [data-ng-app]'));

							// Check if main content areas have loaded
							const mainSelectors = ['main', '[role="main"]', '#main', '.main', '#app', '[id*="app"]'];
							const hasMainContent = mainSelectors.some(sel => {
								const el = document.querySelector(sel);
								return el && el.children.length > 0 && el.textContent.trim().length > 100;
							});

							// Check for route-based content
							const hasRouterContent = !!(
								document.querySelector('[class*="route" i], [class*="page" i], [class*="view" i]') &&
								document.body.textContent.trim().length > 200
							);

							return {
								isSPA: hasReact || hasVue || hasAngular,
								hasMainContent,
								hasRouterContent,
								contentReady: hasMainContent || hasRouterContent
							};
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)

				spa_data = result.get('result', {}).get('value', {})

				# If it's not a SPA or content is ready, we're done
				if not spa_data.get('isSPA', False) or spa_data.get('contentReady', False):
					if spa_data.get('isSPA', False):
						self.logger.debug("✅ SPA content loaded")
					break

				await asyncio.sleep(check_interval)
				elapsed += check_interval

		except Exception as e:
			self.logger.debug(f"SPA content check failed: {e}")

	async def _wait_for_lazy_loaded_images(self, session, timeout: float = 5.0) -> None:
		"""Wait for lazy-loaded images to complete loading."""
		try:
			elapsed = 0.0
			check_interval = 0.5

			while elapsed < timeout:
				result = await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							const images = Array.from(document.images);
							const lazyImages = images.filter(img =>
								img.loading === 'lazy' ||
								img.classList.contains('lazy') ||
								img.hasAttribute('data-src')
							);

							if (lazyImages.length === 0) return { hasLazyImages: false, allLoaded: true };

							const loadedCount = lazyImages.filter(img => img.complete || img.naturalWidth > 0).length;

							return {
								hasLazyImages: true,
								totalLazy: lazyImages.length,
								loadedLazy: loadedCount,
								allLoaded: loadedCount === lazyImages.length
							};
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)

				image_data = result.get('result', {}).get('value', {})

				if not image_data.get('hasLazyImages', False) or image_data.get('allLoaded', True):
					if image_data.get('hasLazyImages', False):
						self.logger.debug(f"✅ Lazy images loaded ({image_data.get('loadedLazy', 0)}/{image_data.get('totalLazy', 0)})")
					break

				await asyncio.sleep(check_interval)
				elapsed += check_interval

		except Exception as e:
			self.logger.debug(f"Lazy image check failed: {e}")

	async def _wait_for_dynamic_lists(self, session, timeout: float = 5.0) -> None:
		"""Wait for dynamic lists and tables to populate."""
		try:
			initial_result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': '''
					(() => {
						const lists = document.querySelectorAll('ul, ol, table, [class*="list" i], [class*="grid" i]');
						const emptyLists = Array.from(lists).filter(list =>
							list.children.length === 0 ||
							(list.children.length === 1 && list.textContent.trim().length < 20)
						);

						return {
							totalLists: lists.length,
							emptyLists: emptyLists.length,
							hasEmptyLists: emptyLists.length > 0
						};
					})()
					''',
					'returnByValue': True,
				},
				session_id=session.session_id
			)

			initial_data = initial_result.get('result', {}).get('value', {})

			if not initial_data.get('hasEmptyLists', False):
				return  # No empty lists to wait for

			elapsed = 0.0
			check_interval = 0.5

			self.logger.debug(f"⏳ Waiting for dynamic lists to populate ({initial_data.get('emptyLists', 0)} empty)")

			while elapsed < timeout:
				result = await session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': '''
						(() => {
							const lists = document.querySelectorAll('ul, ol, table, [class*="list" i], [class*="grid" i]');
							const emptyLists = Array.from(lists).filter(list =>
								list.children.length === 0 ||
								(list.children.length === 1 && list.textContent.trim().length < 20)
							);

							return {
								totalLists: lists.length,
								emptyLists: emptyLists.length,
								hasEmptyLists: emptyLists.length > 0
							};
						})()
						''',
						'returnByValue': True,
					},
					session_id=session.session_id
				)

				list_data = result.get('result', {}).get('value', {})

				# If fewer empty lists than before, content is loading
				if list_data.get('emptyLists', 0) < initial_data.get('emptyLists', 0):
					self.logger.debug("✅ Dynamic lists populated")
					break

				await asyncio.sleep(check_interval)
				elapsed += check_interval

		except Exception as e:
			self.logger.debug(f"Dynamic list check failed: {e}")