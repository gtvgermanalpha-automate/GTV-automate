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

# Avasam's SUPPLIER API (same platform, clearer docs) passes its auth token
# INSIDE THE JSON BODY - "SessionToken"/"AuthorizationToken"/"AuthKey" - not
# as a header, and the Seller API's "header details" tables are really body
# parameters (Page/Limit appear in the Request Body). So body forms are tried
# first, headers second. Discovered form is cached on the client.
def _auth_candidates(token, customer_id="", consumer_key="", secret_key=""):
    cid_h = {"customerId": customer_id} if customer_id else {}
    cid_b = {"customerId": customer_id} if customer_id else {}
    keys_h = {"Consumer_key": consumer_key, "secret_key": secret_key} if consumer_key else {}
    return [
        ("body:AuthorizationToken", {}, {"AuthorizationToken": token}),
        ("body:SessionToken", {}, {"SessionToken": token}),
        ("body:token", {}, {"token": token}),
        ("body:access_token", {}, {"access_token": token}),
        ("body:Authkey", {}, {"Authkey": token}),
        ("body:Token", {}, {"Token": token}),
        ("header:Bearer", {"Authorization": f"Bearer {token}"}, {}),
        ("header:Authkey", {"Authkey": token}, {}),
        ("header:token", {"token": token}, {}),
        ("header:access_token", {"access_token": token}, {}),
        ("header:AuthorizationToken", {"AuthorizationToken": token}, {}),
        ("both:Bearer+AuthorizationToken", {"Authorization": f"Bearer {token}"},
         {"AuthorizationToken": token}),
        # customerId-bearing forms (auth returns it; docs call it ClientID)
        ("hdr:Bearer+body:customerId", {"Authorization": f"Bearer {token}"}, dict(cid_b)),
        ("hdr:Bearer+hdr:customerId", {"Authorization": f"Bearer {token}", **cid_h}, {}),
        ("body:token+customerId", {}, {"token": token, **cid_b}),
        ("body:AuthorizationToken+customerId", {}, {"AuthorizationToken": token, **cid_b}),
        ("body:SessionToken+Authkey=customerId", {}, {"SessionToken": token,
                                                      "Authkey": customer_id}),
        # keys-on-every-call form (some Avasam docs list them as call headers)
        ("hdr:keys+Bearer", {**keys_h, "Authorization": f"Bearer {token}"}, {}),
        ("hdr:keys only", dict(keys_h), {}),
    ]


class AvasamClient:
    def __init__(self, consumer_key=None, secret_key=None):
        self.consumer_key = consumer_key or os.getenv("AVASAM_CONSUMER_KEY") or ""
        self.secret_key = secret_key or os.getenv("AVASAM_SECRET_KEY") or ""
        self.token = None
        self.expires_at = None
        self.auth_style = None          # learned on the first successful data call
        self.auth_response_keys = []
        self.attempts = []              # (label, status, body snippet, WWW-Authenticate)
        self.client_id = ""
        self.end_point = ""
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
        self.auth_response_keys = sorted(body.keys()) if isinstance(body, dict) else []
        # docs prose also mentions a ClientID / End Point for later calls
        # The auth response's real key is "customerId" (the docs' prose calls
        # it ClientID) - confirmed live 2026-09-01.
        self.client_id = (body.get("customerId") or body.get("CustomerId")
                          or body.get("ClientID") or body.get("clientId")
                          or body.get("client_id") or "")
        self.end_point = (body.get("EndPoint") or body.get("end_point")
                          or body.get("endpoint") or "")
        if not self.token:
            raise AuthError(f"avasam auth returned no token: {str(body)[:200]}")
        return True

    # ---------------- transport ----------------
    def _post(self, path, payload, what):
        """POST a data call, discovering the token transport on first use.
        Avasam accepts the token in the body (supplier-API convention), so
        each candidate may add headers, body fields, or both."""
        url = f"{BASE_URL}{path}"
        cands = _auth_candidates(self.token, self.client_id,
                                 self.consumer_key, self.secret_key)
        if self.auth_style:
            cands = [c for c in cands if c[0] == self.auth_style] or cands
        last = None
        for label, hdr_extra, body_extra in cands:
            headers = {"Content-Type": "application/json"}
            headers.update(hdr_extra)
            body = dict(payload)
            body.update(body_extra)
            if self.client_id:
                body.setdefault("ClientID", self.client_id)

            def _call(headers=headers, body=body):
                r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
                if r.status_code == 429:
                    raise RateLimitError(f"{what} rate limited: {r.text[:200]}")
                if r.status_code >= 500:
                    raise TransientError(f"{what} {r.status_code}: {r.text[:200]}")
                return r

            resp = with_retry(_call, what=what, max_attempts=2)
            self._remember_rate_headers(resp)
            hint = resp.headers.get("WWW-Authenticate") or resp.headers.get("www-authenticate") or ""
            self.attempts.append((label, resp.status_code,
                                  " ".join((resp.text or "").split())[:120], hint))
            if resp.status_code in (401, 403):
                last = f"{label} -> {resp.status_code} {resp.text[:100]}"
                continue
            if resp.status_code >= 400:
                last = f"{label} -> {resp.status_code} {resp.text[:200]}"
                continue
            self.auth_style = label
            return resp.json() if resp.content else None
        raise AuthError(f"{what}: no token transport accepted (last: {last})")

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
