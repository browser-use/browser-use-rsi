"""Enhanced error classification system for better error handling and retry logic."""

import logging
import re
from enum import Enum
from typing import Dict, List, Tuple, Optional, Any

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
	"""Classification of errors for appropriate handling strategies."""

	# Retryable errors - temporary issues that may resolve
	RETRYABLE_NETWORK = "retryable_network"
	RETRYABLE_TIMING = "retryable_timing"
	RETRYABLE_RESOURCE = "retryable_resource"
	RETRYABLE_STALE_ELEMENT = "retryable_stale_element"

	# Permanent errors - unlikely to resolve with retry
	PERMANENT_INVALID_INPUT = "permanent_invalid_input"
	PERMANENT_NOT_FOUND = "permanent_not_found"
	PERMANENT_ACCESS_DENIED = "permanent_access_denied"
	PERMANENT_CONFIGURATION = "permanent_configuration"

	# Unknown/uncategorized errors
	UNKNOWN = "unknown"


class ErrorClassificationResult:
	"""Result of error classification with retry recommendations."""

	def __init__(
		self,
		category: ErrorCategory,
		should_retry: bool,
		retry_delay: float = 0.0,
		max_retries: int = 0,
		user_message: Optional[str] = None,
		technical_details: Optional[str] = None
	):
		self.category = category
		self.should_retry = should_retry
		self.retry_delay = retry_delay
		self.max_retries = max_retries
		self.user_message = user_message or "An error occurred"
		self.technical_details = technical_details or ""


class EnhancedErrorClassifier:
	"""Enhanced error classifier with pattern matching and retry strategies."""

	def __init__(self):
		# Network-related error patterns
		self.network_patterns = [
			(r'net::err_', "Network error"),
			(r'connection.*refuse', "Connection refused"),
			(r'connection.*timeout', "Connection timeout"),
			(r'dns.*not.*resolv', "DNS resolution failed"),
			(r'name.*not.*resolv', "Domain name resolution failed"),
			(r'internet.*disconnect', "Internet connection lost"),
			(r'network.*chang', "Network configuration changed"),
			(r'ssl.*protocol.*error', "SSL/TLS protocol error"),
			(r'cert.*error', "Certificate error"),
			(r'failed to fetch', "Network fetch failed"),
			(r'load.*fail', "Resource load failed")
		]

		# Timing-related error patterns
		self.timing_patterns = [
			(r'timeout', "Operation timed out"),
			(r'wait.*timeout', "Wait operation timed out"),
			(r'element.*not.*found.*within', "Element detection timeout"),
			(r'page.*load.*timeout', "Page load timeout"),
			(r'navigation.*timeout', "Navigation timeout")
		]

		# Resource-related error patterns
		self.resource_patterns = [
			(r'insufficient.*resource', "Insufficient resources"),
			(r'memory.*exceed', "Memory limit exceeded"),
			(r'disk.*full', "Disk space full"),
			(r'too.*many.*request', "Rate limit exceeded"),
			(r'service.*unavailable', "Service temporarily unavailable"),
			(r'server.*error', "Server error"),
			(r'internal.*error', "Internal server error")
		]

		# Stale element error patterns
		self.stale_element_patterns = [
			(r'stale.*element', "Stale element reference"),
			(r'element.*no.*longer.*attach', "Element detached from DOM"),
			(r'node.*not.*found', "DOM node not found"),
			(r'invalid.*node.*id', "Invalid node identifier"),
			(r'element.*reference.*invalid', "Invalid element reference"),
			(r'execution.*context.*destroy', "Execution context destroyed")
		]

		# Permanent invalid input patterns
		self.invalid_input_patterns = [
			(r'invalid.*index', "Invalid element index"),
			(r'element.*index.*not.*found', "Element index not found"),
			(r'parameter.*missing', "Required parameter missing"),
			(r'invalid.*parameter', "Invalid parameter value"),
			(r'malformed.*url', "Malformed URL"),
			(r'invalid.*selector', "Invalid CSS selector")
		]

		# Permanent not found patterns
		self.not_found_patterns = [
			(r'not.*found.*in.*browser.*state', "Element not in browser state"),
			(r'text.*not.*found.*on.*page', "Text not found on page"),
			(r'file.*not.*exist', "File does not exist"),
			(r'path.*not.*exist', "Path does not exist"),
			(r'page.*not.*found', "Page not found (404)")
		]

		# Access denied patterns
		self.access_denied_patterns = [
			(r'access.*denied', "Access denied"),
			(r'permission.*denied', "Permission denied"),
			(r'unauthorized', "Unauthorized access"),
			(r'forbidden', "Forbidden access"),
			(r'authentication.*required', "Authentication required")
		]

		# Configuration error patterns
		self.configuration_patterns = [
			(r'cdp.*client.*not.*initialize', "Browser connection not initialized"),
			(r'browser.*not.*start', "Browser failed to start"),
			(r'invalid.*configuration', "Invalid configuration"),
			(r'missing.*dependency', "Missing dependency"),
			(r'incompatible.*version', "Incompatible version")
		]

	def classify_error(
		self,
		error: Exception,
		action_name: str = "",
		context: Dict[str, Any] = None
	) -> ErrorClassificationResult:
		"""Classify an error and provide retry recommendations."""
		context = context or {}
		error_str = str(error).lower()
		error_type = type(error).__name__

		logger.debug(f"Classifying error: {error_type}: {error_str}")

		# Check for retryable patterns first

		# Network errors - retry with exponential backoff
		for pattern, description in self.network_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.RETRYABLE_NETWORK,
					should_retry=True,
					retry_delay=2.0,
					max_retries=3,
					user_message=f"Network issue: {description}. Retrying...",
					technical_details=error_str
				)

		# Timing errors - retry with increased timeout
		for pattern, description in self.timing_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.RETRYABLE_TIMING,
					should_retry=True,
					retry_delay=3.0,
					max_retries=2,
					user_message=f"Timing issue: {description}. Retrying with longer timeout...",
					technical_details=error_str
				)

		# Resource errors - retry with backoff
		for pattern, description in self.resource_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.RETRYABLE_RESOURCE,
					should_retry=True,
					retry_delay=5.0,
					max_retries=2,
					user_message=f"Resource issue: {description}. Waiting before retry...",
					technical_details=error_str
				)

		# Stale element errors - retry immediately with DOM rebuild
		for pattern, description in self.stale_element_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.RETRYABLE_STALE_ELEMENT,
					should_retry=True,
					retry_delay=0.5,
					max_retries=2,
					user_message=f"Element became stale: {description}. Refreshing and retrying...",
					technical_details=error_str
				)

		# Check for permanent error patterns

		# Invalid input - don't retry
		for pattern, description in self.invalid_input_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.PERMANENT_INVALID_INPUT,
					should_retry=False,
					user_message=f"Invalid input: {description}. Please check your parameters.",
					technical_details=error_str
				)

		# Not found - don't retry
		for pattern, description in self.not_found_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.PERMANENT_NOT_FOUND,
					should_retry=False,
					user_message=f"Not found: {description}. The requested item is not available.",
					technical_details=error_str
				)

		# Access denied - don't retry
		for pattern, description in self.access_denied_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.PERMANENT_ACCESS_DENIED,
					should_retry=False,
					user_message=f"Access denied: {description}. Check permissions or authentication.",
					technical_details=error_str
				)

		# Configuration errors - don't retry
		for pattern, description in self.configuration_patterns:
			if re.search(pattern, error_str, re.IGNORECASE):
				return ErrorClassificationResult(
					category=ErrorCategory.PERMANENT_CONFIGURATION,
					should_retry=False,
					user_message=f"Configuration error: {description}. Check system setup.",
					technical_details=error_str
				)

		# Special handling for specific exception types
		if isinstance(error, TimeoutError):
			return ErrorClassificationResult(
				category=ErrorCategory.RETRYABLE_TIMING,
				should_retry=True,
				retry_delay=3.0,
				max_retries=2,
				user_message="Operation timed out. Retrying with extended timeout...",
				technical_details=error_str
			)

		# Default: Unknown error - be conservative and don't retry
		return ErrorClassificationResult(
			category=ErrorCategory.UNKNOWN,
			should_retry=False,
			user_message="An unexpected error occurred. Please try the action again manually.",
			technical_details=f"{error_type}: {error_str}"
		)

	def get_retry_strategy(self, classification: ErrorClassificationResult, attempt_number: int) -> Tuple[bool, float]:
		"""Get retry strategy based on classification and attempt number."""
		if not classification.should_retry:
			return False, 0.0

		if attempt_number >= classification.max_retries:
			return False, 0.0

		# Adjust delay based on attempt number and error category
		base_delay = classification.retry_delay

		if classification.category == ErrorCategory.RETRYABLE_NETWORK:
			# Exponential backoff for network errors
			delay = base_delay * (2 ** attempt_number)
		elif classification.category == ErrorCategory.RETRYABLE_TIMING:
			# Linear increase for timing errors
			delay = base_delay + (attempt_number * 2.0)
		elif classification.category == ErrorCategory.RETRYABLE_RESOURCE:
			# Exponential backoff with jitter for resource errors
			delay = base_delay * (1.5 ** attempt_number)
			import random
			delay += random.uniform(0, 1.0)  # Add jitter
		elif classification.category == ErrorCategory.RETRYABLE_STALE_ELEMENT:
			# Quick retry for stale elements
			delay = base_delay
		else:
			delay = base_delay

		# Cap maximum delay at 30 seconds
		delay = min(delay, 30.0)

		return True, delay


# Global instance for use throughout the tools service
error_classifier = EnhancedErrorClassifier()