"""
Shopping Skill — order history, reorder, browser profile management, shopping lists.

Gives W.I.N.S.T.O.N. memory of past purchases and the ability to reorder
or load saved shopping lists as browser automation tasks.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.shopping")

# ── Data directory ────────────────────────────────────────────────────
_DATA_DIR = Path.home() / ".winston" / "shopping"


class ShoppingSkill(BaseSkill):
    """Manage order history, reorder past purchases, and maintain shopping lists."""

    name = "shopping"
    description = (
        "Manage shopping: save orders to history, reorder past purchases, "
        "maintain shopping lists, and manage browser login profiles for online shops. "
        "Use this when the user wants to reorder something, check past orders, "
        "manage a shopping list, or save/load a shop login session."
    )
    parameters = {
        "action": (
            "Action to perform: "
            "'save_order' — record a completed order, "
            "'list_orders' — show order history (optionally filtered by shop), "
            "'reorder' — get items from a past order for re-purchasing, "
            "'save_list' — save a reusable shopping list, "
            "'load_list' — load a saved shopping list, "
            "'list_lists' — show all saved shopping lists, "
            "'save_profile' — save browser login session for a shop, "
            "'load_profile' — load saved browser session for a shop, "
            "'list_profiles' — show all saved browser profiles."
        ),
        "shop": "Shop name (e.g. 'tesco', 'amazon', 'rewe'). Used by most actions.",
        "order_data": (
            "(save_order) JSON string or dict with order details: "
            "{items: [{name, qty, price}], total, currency, order_id}"
        ),
        "order_id": "(reorder) ID or index of a past order to reorder.",
        "list_name": "(save_list/load_list) Name for the shopping list.",
        "items": "(save_list) JSON list of items: [{name, qty, note}].",
        "limit": "(list_orders) Max number of orders to return. Default: 10.",
    }

    def __init__(self, config=None, browser_skill=None):
        super().__init__(config)
        self._browser_skill = browser_skill
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        (self._orders_dir).mkdir(parents=True, exist_ok=True)
        (self._lists_dir).mkdir(parents=True, exist_ok=True)

    @property
    def _orders_dir(self) -> Path:
        return _DATA_DIR / "orders"

    @property
    def _lists_dir(self) -> Path:
        return _DATA_DIR / "lists"

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "list_orders")
        actions = {
            "save_order": self._save_order,
            "list_orders": self._list_orders,
            "reorder": self._reorder,
            "save_list": self._save_list,
            "load_list": self._load_list,
            "list_lists": self._list_lists,
            "save_profile": self._save_profile,
            "load_profile": self._load_profile,
            "list_profiles": self._list_profiles,
        }
        handler = actions.get(action)
        if not handler:
            return SkillResult(
                success=False,
                message=f"Unknown shopping action: {action}. Available: {', '.join(actions.keys())}",
            )
        try:
            return handler(**kwargs)
        except Exception as e:
            logger.error(f"Shopping action '{action}' failed: {e}")
            return SkillResult(success=False, message=f"Shopping error: {e}")

    # ── Order History ─────────────────────────────────────────────────

    def _save_order(self, **kwargs) -> SkillResult:
        """Record a completed order to history."""
        shop = kwargs.get("shop", "unknown").lower().strip()
        order_data = kwargs.get("order_data", {})

        if isinstance(order_data, str):
            try:
                order_data = json.loads(order_data)
            except json.JSONDecodeError:
                return SkillResult(success=False, message="Invalid order_data JSON.")

        if not order_data:
            return SkillResult(success=False, message="No order_data provided.")

        # Build order record
        order = {
            "shop": shop,
            "timestamp": datetime.now().isoformat(),
            "items": order_data.get("items", []),
            "total": order_data.get("total"),
            "currency": order_data.get("currency", "EUR"),
            "order_id": order_data.get("order_id"),
            "notes": order_data.get("notes", ""),
        }

        # Use timestamp-based filename for uniqueness
        filename = f"{shop}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = self._orders_dir / filename
        path.write_text(json.dumps(order, indent=2, ensure_ascii=False))

        item_count = len(order["items"])
        total = order.get("total", "?")
        currency = order.get("currency", "EUR")
        return SkillResult(
            success=True,
            message=f"Order saved: {item_count} items, {total} {currency} from {shop}.",
            data={"path": str(path), "order": order},
        )

    def _list_orders(self, **kwargs) -> SkillResult:
        """List past orders, optionally filtered by shop."""
        shop_filter = kwargs.get("shop", "").lower().strip()
        limit = int(kwargs.get("limit", 10))

        order_files = sorted(self._orders_dir.glob("*.json"), reverse=True)
        orders = []
        for f in order_files:
            try:
                order = json.loads(f.read_text())
                if shop_filter and order.get("shop", "").lower() != shop_filter:
                    continue
                order["_file"] = f.stem
                orders.append(order)
                if len(orders) >= limit:
                    break
            except Exception:
                continue

        if not orders:
            filter_msg = f" for '{shop_filter}'" if shop_filter else ""
            return SkillResult(
                success=True,
                message=f"No orders found{filter_msg}.",
                data={"orders": []},
            )

        lines = [f"**Order History** ({len(orders)} orders):"]
        for i, o in enumerate(orders):
            items_summary = ", ".join(
                f"{it.get('name', '?')} x{it.get('qty', 1)}"
                for it in o.get("items", [])[:5]
            )
            remaining = len(o.get("items", [])) - 5
            if remaining > 0:
                items_summary += f" (+{remaining} more)"
            total = o.get("total", "?")
            currency = o.get("currency", "EUR")
            date = o.get("timestamp", "?")[:10]
            lines.append(f"  {i+1}. [{date}] **{o.get('shop', '?')}** — {items_summary} — {total} {currency}")

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data={"orders": orders},
            speak=False,
        )

    def _reorder(self, **kwargs) -> SkillResult:
        """Get items from a past order for reordering."""
        shop_filter = kwargs.get("shop", "").lower().strip()
        order_id = kwargs.get("order_id", "")

        # If order_id is a number, treat as index (1-based)
        order_files = sorted(self._orders_dir.glob("*.json"), reverse=True)
        orders = []
        for f in order_files:
            try:
                order = json.loads(f.read_text())
                if shop_filter and order.get("shop", "").lower() != shop_filter:
                    continue
                order["_file"] = f.stem
                orders.append(order)
            except Exception:
                continue

        if not orders:
            return SkillResult(
                success=False,
                message=f"No past orders found{' for ' + shop_filter if shop_filter else ''}.",
            )

        # Find the specific order
        target = None
        if order_id:
            # Try index
            try:
                idx = int(order_id) - 1
                if 0 <= idx < len(orders):
                    target = orders[idx]
            except ValueError:
                pass
            # Try matching order_id field
            if not target:
                for o in orders:
                    if o.get("order_id") == order_id:
                        target = o
                        break
        else:
            # Default to most recent order
            target = orders[0]

        if not target:
            return SkillResult(
                success=False,
                message=f"Order '{order_id}' not found.",
            )

        items = target.get("items", [])
        shop = target.get("shop", "unknown")
        date = target.get("timestamp", "?")[:10]

        lines = [f"**Reorder from {shop}** (originally {date}):"]
        for it in items:
            lines.append(f"  - {it.get('name', '?')} x{it.get('qty', 1)}")

        lines.append(f"\nTotal was: {target.get('total', '?')} {target.get('currency', 'EUR')}")
        lines.append("\nTo proceed, use the browser to navigate to the shop and add these items.")

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data={"shop": shop, "items": items, "original_order": target},
        )

    # ── Shopping Lists ────────────────────────────────────────────────

    def _save_list(self, **kwargs) -> SkillResult:
        """Save a reusable shopping list."""
        list_name = kwargs.get("list_name", "").strip()
        items = kwargs.get("items", [])

        if not list_name:
            return SkillResult(success=False, message="No list_name provided.")

        if isinstance(items, str):
            try:
                items = json.loads(items)
            except json.JSONDecodeError:
                return SkillResult(success=False, message="Invalid items JSON.")

        if not items:
            return SkillResult(success=False, message="No items provided.")

        safe_name = list_name.lower().replace(" ", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        path = self._lists_dir / f"{safe_name}.json"
        data = {
            "name": list_name,
            "items": items,
            "updated_at": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

        return SkillResult(
            success=True,
            message=f"Shopping list '{list_name}' saved with {len(items)} items.",
            data={"path": str(path), "list": data},
        )

    def _load_list(self, **kwargs) -> SkillResult:
        """Load a saved shopping list."""
        list_name = kwargs.get("list_name", "").strip()
        if not list_name:
            return SkillResult(success=False, message="No list_name provided.")

        safe_name = list_name.lower().replace(" ", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        path = self._lists_dir / f"{safe_name}.json"

        if not path.exists():
            return SkillResult(
                success=False,
                message=f"Shopping list '{list_name}' not found.",
            )

        data = json.loads(path.read_text())
        items = data.get("items", [])

        lines = [f"**Shopping List: {data.get('name', list_name)}** ({len(items)} items):"]
        for it in items:
            note = f" ({it.get('note', '')})" if it.get("note") else ""
            lines.append(f"  - {it.get('name', '?')} x{it.get('qty', 1)}{note}")

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data={"list": data},
            speak=False,
        )

    def _list_lists(self, **kwargs) -> SkillResult:
        """Show all saved shopping lists."""
        list_files = sorted(self._lists_dir.glob("*.json"))
        if not list_files:
            return SkillResult(success=True, message="No shopping lists saved yet.")

        lines = [f"**Saved Shopping Lists** ({len(list_files)}):"]
        for f in list_files:
            try:
                data = json.loads(f.read_text())
                name = data.get("name", f.stem)
                count = len(data.get("items", []))
                updated = data.get("updated_at", "?")[:10]
                lines.append(f"  - **{name}** — {count} items (updated {updated})")
            except Exception:
                lines.append(f"  - {f.stem} (could not read)")

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data={"lists": [f.stem for f in list_files]},
            speak=False,
        )

    # ── Browser Profile Management ────────────────────────────────────

    def _save_profile(self, **kwargs) -> SkillResult:
        """Save the current browser session for a shop."""
        shop = kwargs.get("shop", "").strip()
        if not shop:
            return SkillResult(success=False, message="No shop name provided.")

        if not self._browser_skill:
            return SkillResult(
                success=False,
                message="Browser skill not available. Cannot save profile.",
            )

        if self._browser_skill.save_profile(shop):
            return SkillResult(
                success=True,
                message=f"Browser session saved for '{shop}'. Login will be remembered.",
            )
        return SkillResult(
            success=False,
            message=f"Failed to save browser session for '{shop}'.",
        )

    def _load_profile(self, **kwargs) -> SkillResult:
        """Load a saved browser session for a shop."""
        shop = kwargs.get("shop", "").strip()
        if not shop:
            return SkillResult(success=False, message="No shop name provided.")

        if not self._browser_skill:
            return SkillResult(
                success=False,
                message="Browser skill not available. Cannot load profile.",
            )

        # Ensure browser is launched with the profile
        self._browser_skill._ensure_browser(profile=shop)
        if self._browser_skill._active_profile == shop:
            return SkillResult(
                success=True,
                message=f"Browser session for '{shop}' loaded. You should be logged in.",
            )
        return SkillResult(
            success=True,
            message=f"No saved session for '{shop}'. Starting fresh — you may need to log in.",
        )

    def _list_profiles(self, **kwargs) -> SkillResult:
        """List all saved browser profiles."""
        if not self._browser_skill:
            return SkillResult(
                success=False,
                message="Browser skill not available.",
            )

        profiles = self._browser_skill.list_profiles()
        if not profiles:
            return SkillResult(success=True, message="No saved browser profiles.")

        lines = [f"**Saved Browser Profiles** ({len(profiles)}):"]
        for p in profiles:
            name = p.get("name", "?")
            saved = p.get("saved_at", "?")
            if isinstance(saved, str) and len(saved) > 10:
                saved = saved[:10]
            cookies = p.get("cookies", 0)
            lines.append(f"  - **{name}** — {cookies} cookies (saved {saved})")

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data={"profiles": profiles},
            speak=False,
        )
