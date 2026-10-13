from typing import Any
import httpx


class OdooAPIError(RuntimeError):
    pass


class OdooClient:
    def __init__(
        self,
        *,
        base_url: str,
        database: str,
        api_key: str,
        timeout_seconds: float = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @property
    def headers(self):
        return {
            "Authorization": f"bearer {self.api_key}",
            "X-Odoo-Database": self.database,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "claude-odoo-mcp-readonly/1.0",
        }

    async def call(
        self,
        model: str,
        method: str,
        arguments: dict[str, Any] | None = None,
    ):
        url = f"{self.base_url}/json/2/{model}/{method}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=arguments or {},
                )

        except httpx.TimeoutException as exc:
            raise OdooAPIError(
                "Odoo request timed out."
            ) from exc

        except httpx.RequestError as exc:
            raise OdooAPIError(
                f"Unable to reach Odoo: {exc}"
            ) from exc

        if response.status_code in {301, 302, 307, 308}:
            raise OdooAPIError(
                "Odoo redirected the request. Verify ODOO_URL."
            )

        if response.is_error:
            raise OdooAPIError(
                f"Odoo HTTP {response.status_code}: "
                f"{response.text[:1000]}"
            )

        try:
            return response.json()

        except ValueError as exc:
            raise OdooAPIError(
                "Odoo returned non-JSON content."
            ) from exc


    async def authenticate_user(
        self,
        *,
        login: str,
        password: str,
    ) -> dict[str, Any]:
        """Authenticate an interactive user against Odoo.

        Credentials are sent only to Odoo's standard web session endpoint and
        are never stored by this client. The service API key is then used to
        read the authenticated res.users record so the MCP token can carry a
        stable Odoo identity.
        """
        url = f"{self.base_url}/web/session/authenticate"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "db": self.database,
                "login": login,
                "password": password,
            },
            "id": None,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    url,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "zenbiz-odoo-mcp-auth/1.0",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise OdooAPIError("Odoo login request timed out.") from exc
        except httpx.RequestError as exc:
            raise OdooAPIError(f"Unable to reach Odoo login: {exc}") from exc

        if response.status_code in {301, 302, 307, 308}:
            raise OdooAPIError(
                "Odoo redirected the login request. Verify ODOO_URL."
            )

        if response.is_error:
            raise OdooAPIError(
                f"Odoo login returned HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OdooAPIError("Odoo login returned non-JSON content.") from exc

        if payload.get("error"):
            # Deliberately do not expose Odoo's detailed exception to the browser.
            raise OdooAPIError("Invalid Odoo username or password.")

        result = payload.get("result")
        if not isinstance(result, dict):
            raise OdooAPIError("Invalid Odoo username or password.")

        uid = result.get("uid")
        if not isinstance(uid, int) or uid <= 0:
            raise OdooAPIError("Invalid Odoo username or password.")

        users = await self.search_read(
            model="res.users",
            domain=[["id", "=", uid]],
            fields=["id", "name", "login", "email", "active"],
            limit=1,
        )
        if not users:
            raise OdooAPIError("Authenticated Odoo user could not be loaded.")

        user = users[0]
        if not user.get("active", False):
            raise OdooAPIError("This Odoo user is inactive.")

        return {
            "id": user.get("id", uid),
            "name": user.get("name") or result.get("name"),
            "login": user.get("login") or login,
            "email": user.get("email") or user.get("login") or login,
            "active": True,
        }

    async def search_read(
        self,
        *,
        model: str,
        domain: list,
        fields: list[str],
        limit: int,
        offset: int = 0,
        order: str | None = None,
    ):
        body = {
            "domain": domain,
            "fields": fields,
            "limit": limit,
            "offset": offset,
        }

        if order:
            body["order"] = order

        result = await self.call(
            model,
            "search_read",
            body,
        )

        if not isinstance(result, list):
            raise OdooAPIError(
                f"Unexpected response from {model}.search_read."
            )

        return result

    async def read(
        self,
        *,
        model: str,
        record_ids: list[int],
        fields: list[str],
    ):
        result = await self.call(
            model,
            "read",
            {
                "ids": record_ids,
                "fields": fields,
            },
        )

        if not isinstance(result, list):
            raise OdooAPIError(
                f"Unexpected response from {model}.read."
            )

        return result

    async def create(
        self,
        *,
        model: str,
        values: dict[str, Any],
    ):
        """
        Create one record in Odoo.
        """

        result = await self.call(
            model,
            "create",
            {
                "vals_list": values,
            },
        )

        if isinstance(result, list):
            if not result:
                raise OdooAPIError(
                    f"{model}.create returned an empty result."
                )

            return result[0]

        if not isinstance(result, int):
            raise OdooAPIError(
                f"Unexpected response from {model}.create: {result}"
            )

        return result

    async def execute(
        self,
        model: str,
        method: str,
        record_ids: list[int],
        **kwargs,
    ):
        """
        Execute a record method such as sale.order.action_confirm().
        """

        body: dict[str, Any] = {
            "ids": record_ids,
        }

        body.update(kwargs)

        return await self.call(
            model,
            method,
            body,
        )
