"""
Safety & Guardrails Module for W.I.N.S.T.O.N.
Prevents the LLM from executing dangerous actions without explicit user consent.
Provides input validation, action confirmation, rate limiting, and output filtering.
"""

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("winston.safety")


class RiskLevel(Enum):
    """Risk classification for actions."""
    SAFE = "safe"            # No confirmation needed (time, date, notes read)
    LOW = "low"              # Logged but auto-approved (web search, system info)
    MEDIUM = "medium"        # Requires confirmation in interactive mode (open app, take note)
    HIGH = "high"            # Always requires confirmation (send email, run command)
    BLOCKED = "blocked"      # Never allowed (destructive commands, sensitive data access)


class RiskOverride(Enum):
    """Override behavior for a specific request."""
    NONE = "none"
    AUTONOMOUS = "autonomous"  # Auto-approve HIGH risk (except BLOCKED)
    CAREFUL = "careful"        # Force confirmation + screenshot for MEDIUM/HIGH


@dataclass
class ActionRequest:
    """A pending action that may need user confirmation."""
    id: str
    skill_name: str
    action: str
    parameters: dict
    risk_level: RiskLevel
    description: str
    approved: bool = False
    screenshot_path: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> str:
        """Human-readable summary of this action."""
        return f"[{self.risk_level.value.upper()}] {self.description}"


# ── Risk classification rules ──

SKILL_RISK_MAP: dict[str, dict[str, RiskLevel]] = {
    "email": {
        "send": RiskLevel.HIGH,
        "read": RiskLevel.LOW,
        "search": RiskLevel.LOW,
        "_default": RiskLevel.MEDIUM,
    },
    "system_control": {
        "time": RiskLevel.SAFE,
        "date": RiskLevel.SAFE,
        "system_info": RiskLevel.LOW,
        "open_app": RiskLevel.MEDIUM,
        "run_command": RiskLevel.HIGH,
        "_default": RiskLevel.MEDIUM,
    },
    "web_search": {
        "search": RiskLevel.SAFE,
        "_default": RiskLevel.SAFE,   # web search is always safe
    },
    "notes": {
        "read": RiskLevel.SAFE,
        "list": RiskLevel.SAFE,
        "create": RiskLevel.LOW,
        "delete": RiskLevel.MEDIUM,
        "search": RiskLevel.SAFE,
        "_default": RiskLevel.LOW,
    },
    "screenshot": {
        "capture": RiskLevel.MEDIUM,
        "analyze": RiskLevel.LOW,
        "read_text": RiskLevel.LOW,
        "_default": RiskLevel.MEDIUM,
    },
    "youtube": {
        "search": RiskLevel.SAFE,
        "play": RiskLevel.LOW,
        "info": RiskLevel.SAFE,
        "_default": RiskLevel.SAFE,
    },
    "file_manager": {
        "search": RiskLevel.LOW,
        "list": RiskLevel.SAFE,
        "read": RiskLevel.LOW,
        "create": RiskLevel.MEDIUM,
        "info": RiskLevel.SAFE,
        "size": RiskLevel.SAFE,
        "_default": RiskLevel.LOW,
    },
    "clipboard": {
        "read": RiskLevel.SAFE,
        "copy": RiskLevel.SAFE,
        "history": RiskLevel.SAFE,
        "transform": RiskLevel.SAFE,
        "_default": RiskLevel.SAFE,
    },
    "calendar": {
        "create_event": RiskLevel.LOW,
        "list_events": RiskLevel.SAFE,
        "remind": RiskLevel.LOW,
        "list_reminders": RiskLevel.SAFE,
        "briefing": RiskLevel.SAFE,
        "_default": RiskLevel.LOW,
    },
    "smart_home": {
        "open_url": RiskLevel.LOW,
        "bookmark": RiskLevel.SAFE,
        "list_bookmarks": RiskLevel.SAFE,
        "device_control": RiskLevel.MEDIUM,
        "list_devices": RiskLevel.SAFE,
        "_default": RiskLevel.LOW,
    },
    "code_runner": {
        "run": RiskLevel.MEDIUM,
        "explain": RiskLevel.SAFE,
        "_default": RiskLevel.MEDIUM,
    },
    "price_monitor": {
        "watch": RiskLevel.LOW,
        "unwatch": RiskLevel.LOW,
        "list": RiskLevel.SAFE,
        "check": RiskLevel.SAFE,
        "check_all": RiskLevel.SAFE,
        "status": RiskLevel.SAFE,
        "_default": RiskLevel.LOW,
    },
    "browser": {
        "open_page": RiskLevel.LOW,
        "click": RiskLevel.MEDIUM,
        "click_text": RiskLevel.MEDIUM,
        "select_option": RiskLevel.MEDIUM,
        "fill": RiskLevel.MEDIUM,
        "extract_text": RiskLevel.LOW,
        "get_page_structure": RiskLevel.LOW,
        "screenshot_page": RiskLevel.LOW,
        "run_script": RiskLevel.HIGH,
        # Snapshot + ref-based actions
        "snapshot": RiskLevel.LOW,
        "click_ref": RiskLevel.MEDIUM,
        "type_ref": RiskLevel.MEDIUM,
        "select_ref": RiskLevel.MEDIUM,
        "hover_ref": RiskLevel.LOW,
        "scroll": RiskLevel.LOW,
        "_default": RiskLevel.MEDIUM,
    },
    "knowledge_base": {
        "save": RiskLevel.LOW,
        "search": RiskLevel.SAFE,
        "list": RiskLevel.SAFE,
        "get": RiskLevel.SAFE,
        "delete": RiskLevel.MEDIUM,
        "_default": RiskLevel.SAFE,
    },
    "desktop_screenshot": {
        "capture": RiskLevel.LOW,
        "analyze": RiskLevel.LOW,
        "read_text": RiskLevel.LOW,
        "_default": RiskLevel.LOW,
    },
}

# Patterns that should NEVER be in commands, even whitelisted ones
BLOCKED_PATTERNS = [
    r";\s*rm\b",         # chained rm
    r"\|\s*rm\b",        # piped rm
    r"&&\s*rm\b",        # chained rm
    r">\s*/",            # redirect overwriting system paths
    r"rm\s+-rf",         # recursive force delete
    r"mkfs",             # format filesystem
    r"dd\s+if=",         # raw disk write
    r"chmod\s+777",      # wide-open permissions
    r"curl.*\|\s*sh",    # download & execute
    r"wget.*\|\s*sh",    # download & execute
    r"eval\s*\(",        # code eval
    r"sudo\b",           # privilege escalation
    r"\.\./\.\.",        # path traversal
    r"/etc/passwd",      # sensitive file access
    r"/etc/shadow",      # sensitive file access
    r"~/.ssh",           # SSH key access
    r"\.env",            # env file access via commands
    r"password|passwd|secret|token|api.?key",  # credential-seeking patterns
]

# What kind of data the LLM should never include in a response
SENSITIVE_OUTPUT_PATTERNS = [
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b.*password",  # email+password combos
    r"(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*\S+",     # leaked keys
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",                           # private keys
]


class SafetyGuard:
    """
    Central safety controller.
    All skill executions pass through here for risk assessment and confirmation.
    """

    def __init__(self, require_confirmation: bool = True):
        """
        Args:
            require_confirmation: If True, HIGH-risk actions need explicit approval.
                                  Set to False only for fully trusted local-only usage.
        """
        self.require_confirmation = require_confirmation
        self._pending_actions: dict[str, ActionRequest] = {}
        self._action_log: list[dict] = []
        self._rate_limits: dict[str, list[float]] = {}
        self._max_actions_per_minute = 10
        # Per-skill overrides for rate limits (browser needs more throughput)
        self._rate_limit_overrides: dict[str, int] = {
            "browser": 60,  # Browser agent does rapid sequential actions
        }
        logger.info(f"Safety guard initialized (confirmation={require_confirmation})")

    # ── Risk Assessment ──

    def classify_risk(self, skill_name: str, parameters: dict) -> RiskLevel:
        """Classify the risk level of a skill action."""
        action = parameters.get("action", "")

        # Check skill-specific risk map
        if skill_name in SKILL_RISK_MAP:
            skill_map = SKILL_RISK_MAP[skill_name]
            if action and action in skill_map:
                risk = skill_map[action]
            else:
                # Use skill's default risk, or MEDIUM if no default defined
                risk = skill_map.get("_default", RiskLevel.MEDIUM)
        else:
            # Unknown skill defaults to MEDIUM
            risk = RiskLevel.MEDIUM

        # Escalate risk if command contains blocked patterns
        if skill_name == "system_control" and action == "run_command":
            command = parameters.get("command", "")
            if self._contains_blocked_pattern(command):
                risk = RiskLevel.BLOCKED

        # Escalate risk for email to unknown recipients
        if skill_name == "email" and action == "send":
            risk = RiskLevel.HIGH  # Always HIGH for sending emails

        return risk

    def _contains_blocked_pattern(self, text: str) -> bool:
        """Check if text contains any blocked pattern."""
        text_lower = text.lower()
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                logger.warning(f"Blocked pattern detected: '{pattern}' in '{text}'")
                return True
        return False

    # ── Action Gating ──

    def request_action(
        self, 
        skill_name: str, 
        parameters: dict, 
        override: RiskOverride = RiskOverride.NONE
    ) -> ActionRequest:
        """
        Evaluate a skill execution request.
        Returns an ActionRequest with approval status.
        """
        risk = self.classify_risk(skill_name, parameters)
        
        # Apply overrides
        if override == RiskOverride.AUTONOMOUS:
            if risk != RiskLevel.BLOCKED:
                logger.info(f"AUTONOMOUS override: auto-approving {risk.value} risk action")
                risk = RiskLevel.LOW  # Downgrade to auto-approve
        elif override == RiskOverride.CAREFUL:
            if risk in (RiskLevel.SAFE, RiskLevel.LOW):
                logger.info(f"CAREFUL override: escalating {risk.value} to MEDIUM to force confirmation")
                risk = RiskLevel.MEDIUM
            else:
                logger.info(f"CAREFUL override: ensuring confirmation for {risk.value} risk action")
        
        description = self._describe_action(skill_name, parameters)
        action_id = secrets.token_hex(8)

        request = ActionRequest(
            id=action_id,
            skill_name=skill_name,
            action=parameters.get("action", "execute"),
            parameters=parameters,
            risk_level=risk,
            description=description,
        )

        # BLOCKED actions are never approved
        if risk == RiskLevel.BLOCKED:
            request.approved = False
            self._log_action(request, "BLOCKED")
            logger.warning(f"BLOCKED action: {description}")
            return request

        # Rate limit check
        if not self._check_rate_limit(skill_name):
            request.approved = False
            request.risk_level = RiskLevel.BLOCKED
            self._log_action(request, "RATE_LIMITED")
            logger.warning(f"Rate limited: {skill_name}")
            return request

        # SAFE and LOW risk - auto-approve
        if risk in (RiskLevel.SAFE, RiskLevel.LOW):
            request.approved = True
            self._log_action(request, "AUTO_APPROVED")
            return request

        # MEDIUM risk - auto-approve if confirmation is disabled
        if risk == RiskLevel.MEDIUM and not self.require_confirmation:
            request.approved = True
            self._log_action(request, "AUTO_APPROVED")
            return request

        # HIGH and MEDIUM (when confirmation enabled) - needs user confirmation
        self._pending_actions[action_id] = request
        self._log_action(request, "PENDING_CONFIRMATION")
        return request

    def approve_action(self, action_id: str) -> bool:
        """Approve a pending action."""
        if action_id in self._pending_actions:
            action = self._pending_actions.pop(action_id)
            action.approved = True
            self._log_action(action, "USER_APPROVED")
            logger.info(f"Action approved: {action.description}")
            return True
        return False

    def deny_action(self, action_id: str) -> bool:
        """Deny a pending action."""
        if action_id in self._pending_actions:
            action = self._pending_actions.pop(action_id)
            self._log_action(action, "USER_DENIED")
            logger.info(f"Action denied: {action.description}")
            return True
        return False

    def get_pending_actions(self) -> list[ActionRequest]:
        """Get all actions waiting for confirmation."""
        # Expire old pending actions (> 2 minutes)
        now = time.time()
        expired = [
            aid for aid, a in self._pending_actions.items()
            if now - a.timestamp > 120
        ]
        for aid in expired:
            self._pending_actions.pop(aid, None)

        return list(self._pending_actions.values())

    # ── Rate Limiting ──

    def _check_rate_limit(self, skill_name: str) -> bool:
        """Check if a skill is being called too frequently."""
        now = time.time()
        if skill_name not in self._rate_limits:
            self._rate_limits[skill_name] = []

        # Remove entries older than 60 seconds
        self._rate_limits[skill_name] = [
            t for t in self._rate_limits[skill_name] if now - t < 60
        ]

        if len(self._rate_limits[skill_name]) >= self._rate_limit_overrides.get(skill_name, self._max_actions_per_minute):
            return False

        self._rate_limits[skill_name].append(now)
        return True

    # ── Input Sanitization ──

    def sanitize_input(self, user_input: str) -> str:
        """
        Sanitize user input before sending to the LLM.
        Removes potential prompt injection attempts.
        """
        # Remove attempts to override system prompt
        injection_patterns = [
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"forget\s+(all\s+)?previous\s+(instructions|context)",
            r"you\s+are\s+now\s+(?!winston)",
            r"system\s*:\s*",
            r"<\|system\|>",
            r"\[INST\]",
            r"\[/INST\]",
        ]

        sanitized = user_input
        for pattern in injection_patterns:
            if re.search(pattern, sanitized, re.IGNORECASE):
                logger.warning(f"Potential prompt injection detected: '{pattern}'")
                sanitized = re.sub(pattern, "[filtered]", sanitized, flags=re.IGNORECASE)

        return sanitized

    def filter_output(self, response: str) -> str:
        """
        Filter LLM output to prevent leaking sensitive information.
        """
        filtered = response
        for pattern in SENSITIVE_OUTPUT_PATTERNS:
            if re.search(pattern, filtered, re.IGNORECASE):
                logger.warning("Sensitive data detected in LLM output, filtering")
                filtered = re.sub(pattern, "[REDACTED]", filtered, flags=re.IGNORECASE)
        return filtered

    # ── Descriptions ──

    def _describe_action(self, skill_name: str, parameters: dict) -> str:
        """Create a human-readable description of an action."""
        action = parameters.get("action", "execute")

        if skill_name == "email" and action == "send":
            to = parameters.get("to", "unknown")
            subject = parameters.get("subject", "no subject")
            return f"Send email to '{to}' with subject '{subject}'"

        if skill_name == "system_control":
            if action == "run_command":
                cmd = parameters.get("command", "unknown")
                return f"Run system command: '{cmd}'"
            if action == "open_app":
                app = parameters.get("app_name", "unknown")
                return f"Open application: '{app}'"

        if skill_name == "notes" and action == "delete":
            title = parameters.get("title", "unknown")
            return f"Delete note: '{title}'"

        return f"Execute {skill_name}.{action}({parameters})"

    # ── Logging ──

    def _log_action(self, action: ActionRequest, status: str):
        """Log an action for audit trail."""
        entry = {
            "timestamp": time.time(),
            "id": action.id,
            "skill": action.skill_name,
            "action": action.action,
            "risk": action.risk_level.value,
            "status": status,
            "description": action.description,
        }
        self._action_log.append(entry)

        # Keep log bounded (last 500 entries)
        if len(self._action_log) > 500:
            self._action_log = self._action_log[-500:]

    def get_audit_log(self, last_n: int = 20) -> list[dict]:
        """Get the last N entries from the action audit log."""
        return self._action_log[-last_n:]


class WebAuthenticator:
    """
    Simple token-based authentication for the web server.
    Generates a session token on startup that must be presented to use the API.
    """

    def __init__(self, pin: str = None):
        """
        Args:
            pin: Optional 4-6 digit PIN. If None, a random token is generated.
        """
        if pin:
            self.pin = pin
            self.token = hashlib.sha256(pin.encode()).hexdigest()[:32]
        else:
            self.pin = None
            self.token = secrets.token_hex(16)

        self._authenticated_sessions: dict[str, float] = {}  # token → creation timestamp
        self._session_max_age = 86400  # 24 hours
        self._failed_attempts: dict[str, list[float]] = {}
        self._max_failures = 5
        self._lockout_seconds = 300  # 5 minute lockout

    def get_access_token(self) -> str:
        """Get the access token (for display on server startup)."""
        return self.token

    def get_display_pin(self) -> Optional[str]:
        """Get PIN for display if pin-based auth is used."""
        return self.pin

    def authenticate(self, provided: str, client_id: str = "unknown") -> Optional[str]:
        """
        Authenticate using token or PIN.
        Returns a session token on success, None on failure.
        """
        # Check lockout
        if self._is_locked_out(client_id):
            logger.warning(f"Locked out client attempted auth: {client_id}")
            return None

        # Check against token or PIN
        valid = False
        if provided == self.token:
            valid = True
        elif self.pin and provided == self.pin:
            valid = True

        if valid:
            session = secrets.token_hex(16)
            self._authenticated_sessions[session] = time.time()
            # Clear failed attempts on success
            self._failed_attempts.pop(client_id, None)
            logger.info(f"Client authenticated: {client_id}")
            return session
        else:
            self._record_failure(client_id)
            logger.warning(f"Failed auth attempt from: {client_id}")
            return None

    def validate_session(self, session_token: str) -> bool:
        """Check if a session token is valid and not expired."""
        created_at = self._authenticated_sessions.get(session_token)
        if created_at is None:
            return False
        if time.time() - created_at > self._session_max_age:
            # Session expired — clean it up
            self._authenticated_sessions.pop(session_token, None)
            logger.info("Session expired and revoked")
            return False
        return True

    def revoke_session(self, session_token: str):
        """Revoke a session token."""
        self._authenticated_sessions.pop(session_token, None)

    def _record_failure(self, client_id: str):
        """Record a failed authentication attempt."""
        now = time.time()
        if client_id not in self._failed_attempts:
            self._failed_attempts[client_id] = []
        self._failed_attempts[client_id].append(now)

    def _is_locked_out(self, client_id: str) -> bool:
        """Check if a client is locked out due to too many failures."""
        if client_id not in self._failed_attempts:
            return False

        now = time.time()
        # Only count recent attempts
        recent = [
            t for t in self._failed_attempts[client_id]
            if now - t < self._lockout_seconds
        ]
        self._failed_attempts[client_id] = recent
        return len(recent) >= self._max_failures
