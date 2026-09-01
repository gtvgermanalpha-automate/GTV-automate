"""Avasam Seller API client - READ-ONLY surface for the 2026-09-01
supplier-source evaluation (help.avasam.com/docs/seller-api).

Avasam is a UK dropship aggregator. Unlike eBay (one scrape per product)
its catalogue endpoints return up to ~1,000 products per call and it can
push stock/price changes by webhook, so it is far cheaper per refresh.

TOKEN TRANSPORT IS NOT PINNED BY THE DOCS. The auth call is documented
(POST /api/auth/request-token -> access_token + expires_at) but the data
calls only say "with a valid token" - their "header details" tables list
the BODY parameters, and one order call names an `Authkey` header. So
_post() tries the plausible forms in order and remembers the first that
works; probe_avasam.py reports which one, and once confirmed this should
be collapsed to that single form.
"""
import os
import time

import requests

from retry_utils import AuthError, PermanentError, RateLimitError, TransientError, with_retry

BASE_URL = (os.getenv("AVASAM_BASE_URL") or "https://app.avasam.com").rstrip("/")
TIMEOUT = int(os.getenv("AVASAM_TIMEOUT") or "60")

# Tried in order until one returns a non-auth answer; see module docstring.
AUTH_STYLES = ("bearer", "authkey", "token", "access_token")


class AvasamClient:
    def __init__(self, consumer_key=None, secret_key=None):
        self.consumer_key = consumer_key or os.getenv("AVASAM_CONSUMER_KEY") or ""
        self.secret_key = secret_key or os.getenv("AVASAM_SECRET_KEY") or ""
        self.token = None
        self.expires_at = None
        self.auth_style = None          # learned on the first successful data call
        self.last_rate_headers = {}     # docs mention a rate limit but never quantify it

    # ---------------- auth ----------------
    def authenticate(self):
        """POST /api/auth/request-token. Keys are sent as BOTH headers and
        body: the docs call them "header details" then show them in a JSON
        body, so send both rather than guess."""
        if not self.consumer_key or not self.secret_key:
            raise AuthError("AVASAM_CONSUMER_KEY / AVASAM_SECRET_KEY not set")
        url = f"{BASE_URL}/api/auth/request-token"
        payload = {"consumer_key": self.consumer_key, "secret_key": self.secret_key}
        headers = {
            "Content-Type": "application/json",
            "Consumer_key": self.consumer_key,
            "secret_key": self.secret_key,
        }

        def _call():
            r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
            if r.status_code in (401, 403):
                raise AuthError(f"avasam auth rejected ({r.status_code}): {r.text[:200]}")
            if r.status_code == 429:
                raise RateLimitError(f"avasam auth rate limited: {r.text[:200]}")
            if r.status_code >= 500:
                raise TransientError(f"avasam auth {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:
                raise PermanentError(f"avasam auth {r.status_code}: {r.text[:200]}")
            return r

        resp = with_retry(_call, what="avasam auth", max_attempts=3)
        self._remember_rate_headers(resp)
        body = resp.json() if resp.content else {}
        # Response spec says access_token/expires_at; the page's prose also
        # mentions a "ClientID" - accept either shape without assuming.
        self.token = (body.get("access_token") or body.get("token")
                      or body.get("authorisation_token") or "")
        self.expires_at = body.get("expires_at")
        if not self.token:
            raise AuthError(f"avasam auth returned no token: {str(body)[:200]}")
        return True

    def _auth_headers(self, style):
        base = {"Content-Type": "application/json"}
        if style == "bearer":
            base["Authorization"] = f"Bearer {self.token}"
        elif style == "authkey":
            base["Authkey"] = self.token
        elif style == "token":
            base["token"] = self.token
        else:
            base["access_token"] = self.token
        return base

    # ---------------- transport ----------------
    def _post(self, path, payload, what):
        """POST a data call, discovering the token transport on first use."""
        url = f"{BASE_URL}{path}"
        styles = [self.auth_style] if self.auth_style else list(AUTH_STYLES)
        last_err = None
        for style in styles:
            def _call(style=style):
                r = requests.post(url, json=payload, headers=self._auth_headers(style),
                                  timeout=TIMEOUT)
                if r.status_code == 429:
                    raise RateLimitError(f"{what} rate limited: {r.text[:200]}")
                if r.status_code >= 500:
                    raise TransientError(f"{what} {r.status_code}: {r.text[:200]}")
                return r
            resp = with_retry(_call, what=what, max_attempts=3)
            self._remember_rate_headers(resp)
            if resp.status_code in (401, 403):
                last_err = f"{resp.status_code} {resp.text[:120]}"
                time.sleep(0.5)
                continue  # try the next transport form
            if resp.status_code >= 400:
                raise PermanentError(f"{what} {resp.status_code}: {resp.text[:300]}")
            self.auth_style = style
            return resp.json() if resp.content else None
        raise AuthError(f"{what}: no token transport accepted (last: {last_err})")

    def _remember_rate_headers(self, resp):
        self.last_rate_headers = {
            k: v for k, v in resp.headers.items()
            if any(w in k.lower() for w in ("ratelimit", "rate-limit", "retry-after", "quota"))
        }

    # ---------------- read-only catalogue ----------------
    def get_seller_product_list(self, page=0, limit=10):
        """Full product detail incl. BarCode, Description, dimensions."""
        return self._post("/apiseeker/Products/GetSellerProductList",
                          {"Page": page, "Limit": limit}, "avasam product list")

    def get_inventory_with_filter(self, page=0, limit=10, search="", sort_by="SKU",
                                  sort_status="down"):
        """Sourced products WITH stock. `search` matches SKU or title text."""
        return self._post("/apiseeker/ProductModule/GetInventoryListWithFilter",
                          {"ProductType": [], "Supplier": search, "Sortby": sort_by,
                           "SortStatus": sort_status, "limit": str(limit), "page": page},
                          "avasam inventory")

    def seller_stock_list(self, page=0, limit=10):
        return self._post("/apiseeker/Products/SellerStockList",
                          {"limit": limit, "page": page}, "avasam stock list")
