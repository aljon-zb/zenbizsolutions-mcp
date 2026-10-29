from typing import Any, Callable

from fastmcp import FastMCP

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.runtime import PeltierSettings as Settings
from app.addons.peltierpro_odoo.odoo_client import OdooAPIError, OdooClient
from app.addons.peltierpro_odoo.security import clamp_limit, clean_search, positive_id


def register_contacts_tools(
    mcp: FastMCP,
    odoo: OdooClient,
    settings: Settings,
    failed: Callable,
):
    """
    Register Odoo Contacts tools.

    Model:
        res.partner
    """

    # -----------------------------------------------------------------------
    # Contact schema discovery / safe field mapping
    # -----------------------------------------------------------------------
    #
    # IMPORTANT:
    # Odoo Contacts can contain fields added by optional modules or custom
    # Studio/module development. We therefore inspect res.partner with
    # fields_get() at runtime before using optional/additional fields.
    #
    # This keeps the MCP flexible while respecting the current Odoo user's
    # permissions. Binary/image fields and credential-looking custom fields
    # are intentionally not exposed through the broad contact search.
    #

    _partner_field_catalog_cache: dict[str, dict[str, Any]] | None = None

    _preferred_partner_fields = [
        # Identity / hierarchy
        "id",
        "name",
        "display_name",
        "complete_name",
        "active",
        "is_company",
        "type",
        "title",
        "parent_id",
        "commercial_partner_id",
        "company_name",
        "commercial_company_name",
        "company_id",
        "partner_share",

        # Contact information
        "email",
        "email_normalized",
        "phone",
        "mobile",
        "mobile_no",
        "website",
        "linkedin",
        "linkedin_url",

        # Address
        "street",
        "street2",
        "city",
        "state_id",
        "zip",
        "country_id",

        # Business / classification
        "ref",
        "function",
        "vat",
        "company_registry",
        "industry_id",
        "category_id",
        "user_id",
        "team_id",
        "customer_rank",
        "supplier_rank",
        "record_source",

        # Locale
        "lang",
        "tz",

        # Activities / follow-up
        "activity_state",
        "activity_type_id",
        "activity_user_id",
        "activity_date_deadline",
        "activity_summary",
        "activity_exception_decoration",
        "activity_exception_icon",
        "activity_calendar_event_id",

        # Commercial / related counters when installed
        "opportunity_count",
        "meeting_count",
        "sale_order_count",
        "purchase_order_count",
        "total_invoiced",
        "credit",
        "debit",
        "credit_limit",

        # Audit
        "create_uid",
        "create_date",
        "write_uid",
        "write_date",
    ]

    _fallback_partner_fields = [
        "id",
        "name",
        "display_name",
        "active",
        "is_company",
        "type",
        "parent_id",
        "email",
        "phone",
        "mobile_no",
        "website",
        "street",
        "street2",
        "city",
        "state_id",
        "zip",
        "country_id",
        "vat",
        "ref",
        "function",
        "customer_rank",
        "supplier_rank",
        "record_source",
        "create_date",
        "write_date",
    ]

    _safe_return_field_types = {
        "char",
        "text",
        "html",
        "boolean",
        "integer",
        "float",
        "monetary",
        "date",
        "datetime",
        "selection",
        "many2one",
        "many2many",
    }

    _safe_filter_operators = {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "ilike",
        "not ilike",
        "=ilike",
        "in",
        "not in",
        "like",
        "not like",
    }

    _sensitive_field_name_parts = {
        "password",
        "passwd",
        "secret",
        "token",
        "credential",
        "api_key",
        "apikey",
        "private_key",
        "access_key",
    }

    _additional_field_aliases = {
        "job title": "function",
        "job position": "function",
        "position": "function",
        "internal reference": "ref",
        "reference": "ref",
        "record source": "record_source",
        "created on": "create_date",
        "last updated on": "write_date",
        "updated on": "write_date",
        "created by": "create_uid",
        "updated by": "write_uid",
        "next activity": "activity_type_id",
        "next activity date": "activity_date_deadline",
        "next activity deadline": "activity_date_deadline",
        "next activity summary": "activity_summary",
        "activity status": "activity_state",
        "my activity": "activity_user_id",
        "company": "parent_id",
        "parent company": "parent_id",
    }

    def _normalize_field_label(value: Any) -> str:
        return " ".join(
            str(value or "")
            .strip()
            .lower()
            .replace("_", " ")
            .split()
        )

    def _looks_sensitive_field(field_name: str) -> bool:
        normalized = str(field_name or "").strip().lower()
        return any(
            part in normalized
            for part in _sensitive_field_name_parts
        )

    async def _get_partner_field_catalog() -> dict[str, dict[str, Any]]:
        """
        Read res.partner field metadata once per MCP process.

        If field discovery is not available for this Odoo user/database, callers
        fall back to the known core fields instead of breaking all Contacts tools.
        """
        nonlocal _partner_field_catalog_cache

        if _partner_field_catalog_cache is not None:
            return _partner_field_catalog_cache

        try:
            catalog = await odoo.execute(
                "res.partner",
                "fields_get",
                [],
                attributes=[
                    "string",
                    "type",
                    "relation",
                    "readonly",
                ],
            )

            _partner_field_catalog_cache = (
                catalog
                if isinstance(catalog, dict)
                else {}
            )

        except Exception:
            _partner_field_catalog_cache = {}

        return _partner_field_catalog_cache

    async def _resolve_partner_field(
        requested_field: str,
    ) -> tuple[str, dict[str, Any]]:
        """
        Resolve either an Odoo technical field name or a user-facing field label.

        Examples:
        - "record_source" -> record_source
        - "Record Source" -> record_source
        - "Job Position" -> function (when Odoo reports that label)
        """
        requested_field = str(
            requested_field or ""
        ).strip()

        if not requested_field:
            raise ValueError(
                "Additional filter/field name cannot be empty."
            )

        normalized = _normalize_field_label(
            requested_field
        )

        alias_target = _additional_field_aliases.get(
            normalized
        )

        catalog = await _get_partner_field_catalog()

        if not catalog:
            fallback_names = set(
                _fallback_partner_fields
            )
            candidate = alias_target or requested_field

            if candidate in fallback_names:
                return candidate, {
                    "string": candidate,
                    "type": "char",
                }

            raise ValueError(
                f'Cannot validate additional Odoo field "{requested_field}" '
                "because res.partner field discovery is unavailable."
            )

        # 1. Exact technical field name.
        if requested_field in catalog:
            return requested_field, (
                catalog.get(requested_field) or {}
            )

        # 2. Known human-friendly alias.
        if alias_target and alias_target in catalog:
            return alias_target, (
                catalog.get(alias_target) or {}
            )

        # 3. Normalized technical field name.
        technical_matches = [
            field_name
            for field_name in catalog
            if _normalize_field_label(field_name)
            == normalized
        ]

        if len(technical_matches) == 1:
            field_name = technical_matches[0]
            return field_name, (
                catalog.get(field_name) or {}
            )

        # 4. Exact normalized Odoo display label.
        label_matches = [
            field_name
            for field_name, metadata in catalog.items()
            if _normalize_field_label(
                (metadata or {}).get("string")
            )
            == normalized
        ]

        if len(label_matches) == 1:
            field_name = label_matches[0]
            return field_name, (
                catalog.get(field_name) or {}
            )

        if len(label_matches) > 1:
            raise ValueError(
                f'Field label "{requested_field}" matches multiple Odoo fields: '
                + ", ".join(sorted(label_matches))
            )

        raise ValueError(
            f'Odoo contact field "{requested_field}" was not found on res.partner.'
        )

    async def _get_partner_return_fields(
        additional_fields: list[str] | None = None,
        include_available_fields: bool = True,
    ) -> list[str]:
        """
        Return a broad but safe list of contact fields supported by this database.

        Safe custom x_* fields are included automatically when
        include_available_fields=True. Requested additional fields may use either
        the technical name or Odoo's display label.
        """
        catalog = await _get_partner_field_catalog()

        if not catalog:
            fields = list(
                dict.fromkeys(
                    _fallback_partner_fields
                )
            )
        else:
            fields = []

            for field_name in _preferred_partner_fields:
                if field_name not in catalog:
                    continue

                metadata = (
                    catalog.get(field_name) or {}
                )
                field_type = str(
                    metadata.get("type") or ""
                )

                if field_type == "binary":
                    continue

                if _looks_sensitive_field(field_name):
                    continue

                fields.append(field_name)

            if include_available_fields:
                for field_name, metadata in catalog.items():
                    if not str(field_name).startswith("x_"):
                        continue

                    if field_name in fields:
                        continue

                    if _looks_sensitive_field(field_name):
                        continue

                    field_type = str(
                        (metadata or {}).get("type") or ""
                    )

                    if field_type not in _safe_return_field_types:
                        continue

                    fields.append(field_name)

        for requested_field in additional_fields or []:
            field_name, metadata = await _resolve_partner_field(
                requested_field
            )

            if _looks_sensitive_field(field_name):
                raise ValueError(
                    f'Field "{requested_field}" is not exposed by the Contacts search.'
                )

            field_type = str(
                (metadata or {}).get("type") or ""
            )

            if field_type == "binary":
                raise ValueError(
                    f'Binary field "{requested_field}" is not returned by Contacts search.'
                )

            if field_name not in fields:
                fields.append(field_name)

        return fields

    def _text_operator(
        exact_match: bool,
    ) -> str:
        # =ilike gives case-insensitive exact text matching in Odoo.
        return "=ilike" if exact_match else "ilike"

    def _or_domain(
        conditions: list[tuple[Any, Any, Any]],
    ) -> list[Any]:
        """
        Build one Odoo prefix-notation OR expression.

        Example for 3 conditions:
            ["|", "|", condition1, condition2, condition3]
        """
        conditions = [
            condition
            for condition in conditions
            if condition
        ]

        if not conditions:
            return []

        if len(conditions) == 1:
            return [conditions[0]]

        return (
            ["|"] * (len(conditions) - 1)
            + conditions
        )

    async def _available_phone_fields() -> list[str]:
        catalog = await _get_partner_field_catalog()

        candidates = [
            "phone",
            "mobile",
            "mobile_no",
        ]

        if not catalog:
            return [
                "phone",
                "mobile_no",
            ]

        return [
            field_name
            for field_name in candidates
            if field_name in catalog
        ]

    async def _build_general_search_conditions(
        search: str,
        exact_match: bool,
    ) -> list[Any]:
        """
        Legacy/general search across the most useful contact fields.

        This preserves the old search="..." integration while the new first-level
        filters provide more precise multi-filter behavior.
        """
        if not search:
            return []

        operator = _text_operator(
            exact_match
        )

        conditions: list[tuple[Any, Any, Any]] = [
            ("name", operator, search),
            ("email", operator, search),
            ("ref", operator, search),
            ("function", operator, search),
            ("parent_id.name", operator, search),
            ("street", operator, search),
            ("street2", operator, search),
            ("city", operator, search),
            ("state_id.name", operator, search),
            ("zip", operator, search),
            ("country_id.name", operator, search),
        ]

        for field_name in await _available_phone_fields():
            conditions.append(
                (
                    field_name,
                    operator,
                    search,
                )
            )

        return _or_domain(
            conditions
        )

    async def _build_company_filter(
        company: str,
        exact_match: bool,
    ) -> list[Any]:
        if not company:
            return []

        operator = _text_operator(
            exact_match
        )

        catalog = await _get_partner_field_catalog()

        conditions: list[tuple[Any, Any, Any]] = [
            (
                "parent_id.name",
                operator,
                company,
            ),
        ]

        # commercial_company_name is useful on databases/modules that expose it.
        if (
            catalog
            and "commercial_company_name" in catalog
        ):
            conditions.append(
                (
                    "commercial_company_name",
                    operator,
                    company,
                )
            )

        return _or_domain(
            conditions
        )

    async def _build_phone_filter(
        phone: str,
        exact_match: bool,
    ) -> list[Any]:
        if not phone:
            return []

        operator = _text_operator(
            exact_match
        )

        conditions = [
            (
                field_name,
                operator,
                phone,
            )
            for field_name in await _available_phone_fields()
        ]

        return _or_domain(
            conditions
        )

    def _build_address_filter(
        address: str,
        exact_match: bool,
    ) -> list[Any]:
        if not address:
            return []

        operator = _text_operator(
            exact_match
        )

        return _or_domain(
            [
                ("street", operator, address),
                ("street2", operator, address),
                ("city", operator, address),
                ("state_id.name", operator, address),
                ("zip", operator, address),
                ("country_id.name", operator, address),
            ]
        )

    async def _validate_additional_filters(
        additional_filters: list[dict[str, Any]] | None,
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        """
        Validate and convert optional additional Odoo field filters.

        Each item:
        {
            "field": "Record Source",  # technical name OR Odoo display label
            "operator": "ilike",       # optional
            "value": "Trade Show"
        }

        The field must exist on res.partner in this database.
        """
        domain: list[Any] = []
        resolved: list[dict[str, Any]] = []

        for index, raw_filter in enumerate(
            additional_filters or [],
            start=1,
        ):
            if not isinstance(raw_filter, dict):
                raise ValueError(
                    f"additional_filters[{index}] must be an object/dictionary."
                )

            requested_field = str(
                raw_filter.get("field") or ""
            ).strip()

            if not requested_field:
                raise ValueError(
                    f'additional_filters[{index}] requires "field".'
                )

            if "value" not in raw_filter:
                raise ValueError(
                    f'additional_filters[{index}] requires "value".'
                )

            field_name, metadata = await _resolve_partner_field(
                requested_field
            )

            if _looks_sensitive_field(field_name):
                raise ValueError(
                    f'Field "{requested_field}" is not available for flexible search.'
                )

            field_type = str(
                (metadata or {}).get("type") or ""
            )

            default_operator = (
                "ilike"
                if field_type
                in {
                    "char",
                    "text",
                    "html",
                    "selection",
                    "many2one",
                }
                else "="
            )

            operator = str(
                raw_filter.get("operator")
                or default_operator
            ).strip().lower()

            if operator not in _safe_filter_operators:
                raise ValueError(
                    f'Unsupported operator "{operator}" for additional_filters[{index}]. '
                    "Allowed operators: "
                    + ", ".join(
                        sorted(
                            _safe_filter_operators
                        )
                    )
                )

            value = raw_filter.get("value")

            if operator in {"in", "not in"}:
                if not isinstance(
                    value,
                    (list, tuple),
                ):
                    raise ValueError(
                        f'Operator "{operator}" requires a list value '
                        f"for additional_filters[{index}]."
                    )
                value = list(value)

            domain.append(
                (
                    field_name,
                    operator,
                    value,
                )
            )

            resolved.append(
                {
                    "requested_field": requested_field,
                    "field": field_name,
                    "label": (
                        metadata or {}
                    ).get("string"),
                    "type": field_type or None,
                    "operator": operator,
                    "value": value,
                }
            )

        return domain, resolved

    async def _search_partner_page(
        domain: list[Any],
        fields: list[str],
        limit: int,
        offset: int,
        order: str = "name asc",
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        """
        Read one result page.

        Prefer OdooClient.search_read(offset=...) when supported. Older client
        wrappers may not expose offset, so a direct execute fallback is attempted.
        """
        requested_limit = limit + 1

        try:
            rows = await odoo.search_read(
                model="res.partner",
                domain=domain,
                fields=fields,
                limit=requested_limit,
                offset=offset,
                order=order,
            )

        except TypeError as exc:
            # Backward compatibility with older OdooClient.search_read wrappers
            # that do not yet expose an offset keyword.
            if "offset" not in str(exc):
                raise

            try:
                rows = await odoo.execute(
                    "res.partner",
                    "search_read",
                    [domain],
                    fields=fields,
                    offset=offset,
                    limit=requested_limit,
                    order=order,
                )

            except Exception:
                # Last-resort compatibility fallback. This supports small offsets
                # without requiring an unrelated OdooClient change.
                fallback_limit = offset + requested_limit

                rows_from_start = await odoo.search_read(
                    model="res.partner",
                    domain=domain,
                    fields=fields,
                    limit=fallback_limit,
                    order=order,
                )

                rows = rows_from_start[
                    offset:
                    offset + requested_limit
                ]

        rows = (
            rows
            if isinstance(rows, list)
            else []
        )

        page = rows[:limit]
        has_more = len(rows) > limit

        total_count: int | None = None

        try:
            count_value = await odoo.execute(
                "res.partner",
                "search_count",
                [domain],
            )

            if isinstance(
                count_value,
                int,
            ):
                total_count = count_value

        except Exception:
            total_count = None

        if total_count is not None:
            has_more = (
                offset + len(page)
                < total_count
            )

        return (
            page,
            has_more,
            total_count,
        )


    # -----------------------------------------------------------------------
    # Search contacts
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def get_contact_field_catalog():
        """
        Inspect the contact fields available on res.partner in this Odoo database.

        Use this when the user asks for a contact field outside the core search
        parameters (for example Record Source, LinkedIn, an activity field, or a
        custom Studio field).

        Returns readable field names, labels, and data types. This performs no
        write and respects the connected Odoo user's permissions.
        """

        tool = "get_contact_field_catalog"
        params: dict[str, Any] = {}

        try:
            catalog = await _get_partner_field_catalog()

            if not catalog:
                return branded_response(
                    {
                        "success": False,
                        "field_discovery_available": False,
                        "fields": [],
                        "message": (
                            "Odoo res.partner field discovery is not available "
                            "for this connection."
                        ),
                    }
                )

            fields = [
                {
                    "name": field_name,
                    "label": (
                        metadata or {}
                    ).get("string"),
                    "type": (
                        metadata or {}
                    ).get("type"),
                    "relation": (
                        metadata or {}
                    ).get("relation"),
                    "readonly": (
                        metadata or {}
                    ).get("readonly"),
                }
                for field_name, metadata in sorted(
                    catalog.items()
                )
                if (
                    (metadata or {}).get("type")
                    != "binary"
                    and not _looks_sensitive_field(
                        field_name
                    )
                )
            ]

            log_tool(
                tool,
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "field_discovery_available": True,
                    "count": len(fields),
                    "fields": fields,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )

    @mcp.tool()
    async def search_contacts(
        search: str = "",
        name: str = "",
        company: str = "",
        job_title: str = "",
        email: str = "",
        phone: str = "",
        address: str = "",
        internal_reference: str = "",
        active: bool | None = None,
        is_customer: bool | None = None,
        is_vendor: bool | None = None,
        is_company: bool | None = None,
        contact_type: str = "all",
        exact_match: bool = False,
        additional_filters: list[dict[str, Any]] | None = None,
        additional_fields: list[str] | None = None,
        include_available_fields: bool = True,
        active_only: bool = True,
        limit: int = 20,
        offset: int = 0,
    ):
        """
        Flexible Odoo Contacts search.

        CORE FIRST-LEVEL FILTERS
        ------------------------
        Claude should map the user's natural-language request to these parameters.
        The user does NOT need to know Odoo technical field names.

        name:
            Contact/person/company name.

        company:
            Company / parent company associated with the contact.
            For phrases such as "John from ABC Company" or
            "contacts under XYZ Company", use this together with name or
            is_company=False as appropriate.

        job_title:
            Odoo Job Position / function.

        email:
            Contact email.

        phone:
            Searches all supported phone fields (for example phone, mobile,
            mobile_no).

        address:
            Searches street, street2, city, state, ZIP/postal code, and country.

        internal_reference:
            Odoo Internal Reference / ref.

        active:
            True = active contacts only.
            False = inactive contacts only.
            None = use the backward-compatible active_only behavior.

        is_customer:
            True = customer_rank > 0.
            False = customer_rank = 0.
            None = do not filter by customer classification.

        is_vendor:
            True = supplier_rank > 0.
            False = supplier_rank = 0.
            None = do not filter by vendor classification.

        is_company:
            True = company records.
            False = individual/person contacts.
            None = both.

        MULTIPLE FILTERS
        ----------------
        Core filters are combined with AND.

        Examples:
        - "Find all active customers in California"
          -> active=True, is_customer=True, address="California"

        - "Find John from ABC Company"
          -> name="John", company="ABC Company"

        - "Show all vendors under XYZ Company"
          -> company="XYZ Company", is_vendor=True, is_company=False

        - "Find contacts with the job title Accountant"
          -> job_title="Accountant"

        PARTIAL / EXACT MATCHING
        ------------------------
        exact_match=False (default):
            Uses case-insensitive partial matching for text core filters.

        exact_match=True:
            Uses case-insensitive exact text matching.

        LEGACY GENERAL SEARCH
        ---------------------
        search preserves the previous integration. It performs a broad OR search
        across useful fields. If search is combined with core filters, the broad
        search expression is ANDed with those filters.

        ADDITIONAL ODOO FIELDS
        ----------------------
        additional_filters allows filtering any supported/readable res.partner
        field that exists in the current Odoo database.

        Example:
            additional_filters=[
                {
                    "field": "Record Source",
                    "operator": "ilike",
                    "value": "Trade Show",
                }
            ]

        "field" may be an Odoo technical name or a user-facing field label.
        fields_get() is used to verify the real field name/type first.

        additional_fields can request extra fields in the returned contacts.
        include_available_fields=True returns broad safe contact metadata,
        including safe custom x_* fields supported by this database.

        PAGINATION
        ----------
        limit:
            Page size, bounded by the MCP configured maximum.

        offset:
            Number of matching records to skip.

        The response includes has_more and next_offset.

        BACKWARD COMPATIBILITY
        ----------------------
        active_only is preserved for existing integrations. If active is supplied,
        active takes precedence.
        """

        tool = "search_contacts"

        params = {
            "search": search,
            "name": name,
            "company": company,
            "job_title": job_title,
            "email": email,
            "phone": phone,
            "address": address,
            "internal_reference": internal_reference,
            "active": active,
            "is_customer": is_customer,
            "is_vendor": is_vendor,
            "is_company": is_company,
            "contact_type": contact_type,
            "exact_match": exact_match,
            "additional_filters": additional_filters,
            "additional_fields": additional_fields,
            "include_available_fields": include_available_fields,
            "active_only": active_only,
            "limit": limit,
            "offset": offset,
        }

        try:
            search = clean_search(search)
            name = clean_search(name)
            company = clean_search(company)
            job_title = clean_search(job_title)
            email = clean_search(email)
            phone = clean_search(phone)
            address = clean_search(address)
            internal_reference = clean_search(
                internal_reference
            )

            limit = clamp_limit(
                limit,
                settings.max_results,
            )

            try:
                offset = int(offset)
            except (TypeError, ValueError):
                raise ValueError(
                    "offset must be a non-negative integer."
                )

            if offset < 0:
                raise ValueError(
                    "offset must be a non-negative integer."
                )

            allowed_contact_types = {
                "all",
                "contact",
                "invoice",
                "delivery",
                "other",
                "private",
            }

            if contact_type not in allowed_contact_types:
                raise ValueError(
                    "Invalid contact_type. "
                    "Allowed values: "
                    + ", ".join(
                        sorted(
                            allowed_contact_types
                        )
                    )
                )

            text_operator = _text_operator(
                exact_match
            )

            domain: list[Any] = []

            # Backward-compatible general search.
            domain += await _build_general_search_conditions(
                search,
                exact_match,
            )

            # Core first-level parameters. Each supplied core filter is ANDed
            # with the others.
            if name:
                domain.append(
                    (
                        "name",
                        text_operator,
                        name,
                    )
                )

            domain += await _build_company_filter(
                company,
                exact_match,
            )

            if job_title:
                domain.append(
                    (
                        "function",
                        text_operator,
                        job_title,
                    )
                )

            if email:
                domain.append(
                    (
                        "email",
                        text_operator,
                        email,
                    )
                )

            domain += await _build_phone_filter(
                phone,
                exact_match,
            )

            domain += _build_address_filter(
                address,
                exact_match,
            )

            if internal_reference:
                domain.append(
                    (
                        "ref",
                        text_operator,
                        internal_reference,
                    )
                )

            # Active status. Explicit active= wins over legacy active_only.
            if active is not None:
                domain.append(
                    (
                        "active",
                        "=",
                        bool(active),
                    )
                )
            elif active_only:
                domain.append(
                    (
                        "active",
                        "=",
                        True,
                    )
                )

            if is_customer is True:
                domain.append(
                    (
                        "customer_rank",
                        ">",
                        0,
                    )
                )
            elif is_customer is False:
                domain.append(
                    (
                        "customer_rank",
                        "=",
                        0,
                    )
                )

            if is_vendor is True:
                domain.append(
                    (
                        "supplier_rank",
                        ">",
                        0,
                    )
                )
            elif is_vendor is False:
                domain.append(
                    (
                        "supplier_rank",
                        "=",
                        0,
                    )
                )

            if is_company is not None:
                domain.append(
                    (
                        "is_company",
                        "=",
                        is_company,
                    )
                )

            if contact_type != "all":
                domain.append(
                    (
                        "type",
                        "=",
                        contact_type,
                    )
                )

            extra_domain, resolved_extra_filters = (
                await _validate_additional_filters(
                    additional_filters
                )
            )

            domain += extra_domain

            return_fields = await _get_partner_return_fields(
                additional_fields=additional_fields,
                include_available_fields=include_available_fields,
            )

            records, has_more, total_count = (
                await _search_partner_page(
                    domain=domain,
                    fields=return_fields,
                    limit=limit,
                    offset=offset,
                    order="name asc",
                )
            )

            next_offset = (
                offset + len(records)
                if has_more
                else None
            )

            applied_filters = {
                key: value
                for key, value in {
                    "search": search or None,
                    "name": name or None,
                    "company": company or None,
                    "job_title": job_title or None,
                    "email": email or None,
                    "phone": phone or None,
                    "address": address or None,
                    "internal_reference": (
                        internal_reference
                        or None
                    ),
                    "active": (
                        active
                        if active is not None
                        else (
                            True
                            if active_only
                            else None
                        )
                    ),
                    "is_customer": is_customer,
                    "is_vendor": is_vendor,
                    "is_company": is_company,
                    "contact_type": (
                        contact_type
                        if contact_type != "all"
                        else None
                    ),
                    "exact_match": exact_match,
                }.items()
                if value is not None
            }

            log_tool(
                tool,
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "count": len(records),
                    "total_count": total_count,
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                    "next_offset": next_offset,
                    "applied_filters": applied_filters,
                    "resolved_additional_filters": (
                        resolved_extra_filters
                    ),
                    "returned_fields": return_fields,
                    "contacts": records,
                    "message": (
                        f"Returned {len(records)} matching contact(s)"
                        + (
                            f" of {total_count}"
                            if total_count is not None
                            else ""
                        )
                        + "."
                    ),
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # Get contact details
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def get_contact(
        contact_id: int,
    ):
        """
        Get detailed information about a specific Odoo contact.

        Requires the res.partner contact ID.

        Example:
        - Show contact ID 25
        - Get details for contact 103
        """

        params = {
            "contact_id": contact_id,
        }

        try:
            contact_id = positive_id(
                contact_id,
                "contact_id",
            )

            records = await odoo.search_read(
                model="res.partner",
                domain=[
                    (
                        "id",
                        "=",
                        contact_id,
                    )
                ],
                fields=await _get_partner_return_fields(
                    additional_fields=None,
                    include_available_fields=True,
                ),
                limit=1,
            )

            if not records:
                return branded_response(
                    {
                        "success": False,
                        "error": (
                            f"Contact ID "
                            f"{contact_id} "
                            "was not found."
                        ),
                    }
                )

            log_tool(
                "get_contact",
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "contact": records[0],
                }
            )

        except Exception as exc:
            return failed(
                "get_contact",
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # List customers
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def list_customers(
        search: str = "",
        active_only: bool = True,
        limit: int = 20,
    ):
        """
        List customer contacts.

        Customers are identified using:
            customer_rank > 0

        Examples:
        - Show customers
        - Find customer Peltier
        - List active customers
        """

        params = {
            "search": search,
            "active_only": active_only,
            "limit": limit,
        }

        try:
            search = clean_search(search)
            limit = clamp_limit(
                limit,
                settings.max_results,
            )

            domain: list[Any] = [
                (
                    "customer_rank",
                    ">",
                    0,
                )
            ]

            if active_only:
                domain.append(
                    (
                        "active",
                        "=",
                        True,
                    )
                )

            if search:
                domain += [
                    "|",
                    "|",
                    "|",
                    (
                        "name",
                        "ilike",
                        search,
                    ),
                    (
                        "email",
                        "ilike",
                        search,
                    ),
                    (
                        "phone",
                        "ilike",
                        search,
                    ),
                    (
                        "ref",
                        "ilike",
                        search,
                    ),
                ]

            records = await odoo.search_read(
                model="res.partner",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "display_name",
                    "parent_id",
                    "email",
                    "phone",
                    "website",
                    "city",
                    "state_id",
                    "country_id",
                    "vat",
                    "ref",
                    "function",
                    "customer_rank",
                ],
                limit=limit,
                order="name asc",
            )

            log_tool(
                "list_customers",
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "count": len(records),
                    "customers": records,
                }
            )

        except Exception as exc:
            return failed(
                "list_customers",
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # List vendors
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def list_vendors(
        search: str = "",
        active_only: bool = True,
        limit: int = 20,
    ):
        """
        List vendor or supplier contacts.

        Vendors are identified using:
            supplier_rank > 0

        Examples:
        - Show vendors
        - List suppliers
        - Find vendor ABC Company
        """

        params = {
            "search": search,
            "active_only": active_only,
            "limit": limit,
        }

        try:
            search = clean_search(search)
            limit = clamp_limit(
                limit,
                settings.max_results,
            )

            domain: list[Any] = [
                (
                    "supplier_rank",
                    ">",
                    0,
                )
            ]

            if active_only:
                domain.append(
                    (
                        "active",
                        "=",
                        True,
                    )
                )

            if search:
                domain += [
                    "|",
                    "|",
                    "|",
                    (
                        "name",
                        "ilike",
                        search,
                    ),
                    (
                        "email",
                        "ilike",
                        search,
                    ),
                    (
                        "phone",
                        "ilike",
                        search,
                    ),
                    (
                        "ref",
                        "ilike",
                        search,
                    ),
                ]

            records = await odoo.search_read(
                model="res.partner",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "display_name",
                    "parent_id",
                    "email",
                    "phone",
                    "website",
                    "city",
                    "state_id",
                    "country_id",
                    "vat",
                    "ref",
                    "function",
                    "supplier_rank",
                ],
                limit=limit,
                order="name asc",
            )

            log_tool(
                "list_vendors",
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "count": len(records),
                    "vendors": records,
                }
            )

        except Exception as exc:
            return failed(
                "list_vendors",
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # Contacts summary
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def contacts_summary(
        sample_limit: int = 10,
    ):
        """
        Generate a high-level summary of Odoo Contacts.

        Includes:
        - Active contacts
        - Companies
        - Persons
        - Customers
        - Vendors
        - Contacts with email
        - Contacts with phone
        - Sample recently modified contacts

        Example:
        - Give me a Contacts summary
        - Summarize our customers and vendors
        - How many contacts do we have?
        """

        params = {
            "sample_limit": sample_limit,
        }

        try:
            sample_limit = clamp_limit(
                sample_limit,
                settings.max_results,
            )

            #
            # We deliberately use search_read here
            # instead of requiring search_count()
            # from OdooClient.
            #

            active_contacts = await odoo.search_read(
                model="res.partner",
                domain=[
                    (
                        "active",
                        "=",
                        True,
                    )
                ],
                fields=[
                    "id",
                    "is_company",
                    "customer_rank",
                    "supplier_rank",
                    "email",
                    "phone",
                ],
                limit=settings.max_results,
            )

            total_active = len(
                active_contacts
            )

            companies = sum(
                1
                for contact in active_contacts
                if contact.get(
                    "is_company"
                )
                is True
            )

            persons = sum(
                1
                for contact in active_contacts
                if contact.get(
                    "is_company"
                )
                is False
            )

            customers = sum(
                1
                for contact in active_contacts
                if (
                    contact.get(
                        "customer_rank",
                        0,
                    )
                    or 0
                )
                > 0
            )

            vendors = sum(
                1
                for contact in active_contacts
                if (
                    contact.get(
                        "supplier_rank",
                        0,
                    )
                    or 0
                )
                > 0
            )

            with_email = sum(
                1
                for contact in active_contacts
                if contact.get(
                    "email"
                )
            )

            with_phone = sum(
                1
                for contact in active_contacts
                if contact.get(
                    "phone"
                )
            )

            recent_contacts = await odoo.search_read(
                model="res.partner",
                domain=[
                    (
                        "active",
                        "=",
                        True,
                    )
                ],
                fields=[
                    "id",
                    "name",
                    "email",
                    "phone",
                    "customer_rank",
                    "supplier_rank",
                    "write_date",
                ],
                limit=sample_limit,
                order="write_date desc",
            )

            log_tool(
                "contacts_summary",
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "summary": {
                        "total_active_contacts": (
                            total_active
                        ),
                        "companies": companies,
                        "persons": persons,
                        "customers": customers,
                        "vendors": vendors,
                        "with_email": with_email,
                        "with_phone": (
                            with_phone
                        ),
                    },
                    "recent_contacts": (
                        recent_contacts
                    ),
                }
            )

        except Exception as exc:
            return failed(
                "contacts_summary",
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # Company contacts / related contacts
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def get_company_contacts(
        company_id: int,
        active_only: bool = True,
        limit: int = 50,
    ):
        """
        Show contacts belonging to a company.

        Uses the parent_id relationship in res.partner.

        Examples:
        - Show contacts under Peltier Pro
        - Show people belonging to company ID 15
        """

        params = {
            "company_id": company_id,
            "active_only": active_only,
            "limit": limit,
        }

        try:
            company_id = positive_id(
                company_id,
                "company_id",
            )

            limit = clamp_limit(
                limit,
                settings.max_results,
            )

            company = await odoo.search_read(
                model="res.partner",
                domain=[
                    (
                        "id",
                        "=",
                        company_id,
                    )
                ],
                fields=[
                    "id",
                    "name",
                    "is_company",
                ],
                limit=1,
            )

            if not company:
                return branded_response(
                    {
                        "success": False,
                        "error": (
                            f"Company/contact ID "
                            f"{company_id} "
                            "was not found."
                        ),
                    }
                )

            domain: list[Any] = [
                (
                    "parent_id",
                    "=",
                    company_id,
                )
            ]

            if active_only:
                domain.append(
                    (
                        "active",
                        "=",
                        True,
                    )
                )

            contacts = await odoo.search_read(
                model="res.partner",
                domain=domain,
                fields=await _get_partner_return_fields(
                    additional_fields=None,
                    include_available_fields=True,
                ),
                limit=limit,
                order="name asc",
            )

            log_tool(
                "get_company_contacts",
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "company": company[0],
                    "count": len(contacts),
                    "contacts": contacts,
                }
            )

        except Exception as exc:
            return failed(
                "get_company_contacts",
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # Find a person/contact by company and job role
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def find_company_contact_by_role(
        company_name: str,
        role: str,
        active_only: bool = True,
        limit: int = 20,
    ):
        """
        Find people belonging to a company whose Odoo Job Position / function
        matches a requested role.

        This is the preferred tool for questions such as:
        - Who is the CEO of 5 Minute Gourmet?
        - Find the Sr Mechanical Engineer at 3rd Stone Design.
        - Who is the purchasing manager at ABC Company?

        Resolution flow:
        1. Find the company res.partner record by name.
        2. Use parent_id to retrieve people linked to that company.
        3. Match the requested role against the function field.

        The tool does not guess from email domains and does not search the web.
        """

        params = {
            "company_name": company_name,
            "role": role,
            "active_only": active_only,
            "limit": limit,
        }

        try:
            company_name = clean_search(company_name)
            role = clean_search(role)
            limit = clamp_limit(
                limit,
                settings.max_results,
            )

            if not company_name:
                raise ValueError(
                    "company_name is required."
                )

            if not role:
                raise ValueError(
                    "role is required."
                )

            company_fields = [
                "id",
                "name",
                "display_name",
                "is_company",
                "active",
                "website",
                "email",
                "phone",
            ]

            # Prefer an exact case-insensitive company-name match first.
            company_domain: list[Any] = [
                (
                    "name",
                    "=ilike",
                    company_name,
                ),
                (
                    "is_company",
                    "=",
                    True,
                ),
            ]

            if active_only:
                company_domain.append(
                    (
                        "active",
                        "=",
                        True,
                    )
                )

            companies = await odoo.search_read(
                model="res.partner",
                domain=company_domain,
                fields=company_fields,
                limit=10,
                order="name asc",
            )

            # Fall back to a partial company-name match when no exact match exists.
            if not companies:
                company_domain = [
                    (
                        "name",
                        "ilike",
                        company_name,
                    ),
                    (
                        "is_company",
                        "=",
                        True,
                    ),
                ]

                if active_only:
                    company_domain.append(
                        (
                            "active",
                            "=",
                            True,
                        )
                    )

                companies = await odoo.search_read(
                    model="res.partner",
                    domain=company_domain,
                    fields=company_fields,
                    limit=10,
                    order="name asc",
                )

            if not companies:
                log_tool(
                    "find_company_contact_by_role",
                    params,
                    success=True,
                )
                return branded_response(
                    {
                        "success": True,
                        "company_found": False,
                        "company_name": company_name,
                        "requested_role": role,
                        "count": 0,
                        "contacts": [],
                        "message": (
                            f'No Odoo company matched "{company_name}".'
                        ),
                    }
                )

            # If a partial lookup produced multiple companies, do not silently guess.
            if len(companies) > 1:
                exact_matches = [
                    company
                    for company in companies
                    if str(company.get("name") or "").strip().casefold()
                    == company_name.casefold()
                ]

                if len(exact_matches) == 1:
                    companies = exact_matches
                else:
                    log_tool(
                        "find_company_contact_by_role",
                        params,
                        success=True,
                    )
                    return branded_response(
                        {
                            "success": True,
                            "company_found": True,
                            "ambiguous_company": True,
                            "company_name": company_name,
                            "requested_role": role,
                            "company_matches": companies,
                            "count": 0,
                            "contacts": [],
                            "message": (
                                "Multiple Odoo companies matched the requested name. "
                                "Use the exact company name before selecting a person."
                            ),
                        }
                    )

            company = companies[0]
            company_id = positive_id(
                company["id"],
                "company_id",
            )

            contact_domain: list[Any] = [
                (
                    "parent_id",
                    "=",
                    company_id,
                ),
                (
                    "is_company",
                    "=",
                    False,
                ),
                (
                    "function",
                    "ilike",
                    role,
                ),
            ]

            if active_only:
                contact_domain.append(
                    (
                        "active",
                        "=",
                        True,
                    )
                )

            contacts = await odoo.search_read(
                model="res.partner",
                domain=contact_domain,
                fields=await _get_partner_return_fields(
                    additional_fields=None,
                    include_available_fields=True,
                ),
                limit=limit,
                order="name asc",
            )

            log_tool(
                "find_company_contact_by_role",
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "company_found": True,
                    "ambiguous_company": False,
                    "company": company,
                    "requested_role": role,
                    "count": len(contacts),
                    "contacts": contacts,
                    "message": (
                        f'Found {len(contacts)} contact(s) under "{company.get("name")}" '
                        f'with a job title matching "{role}".'
                        if contacts
                        else (
                            f'Company "{company.get("name")}" exists in Odoo, but no linked '
                            f'person has a job title matching "{role}".'
                        )
                    ),
                }
            )

        except Exception as exc:
            return failed(
                "find_company_contact_by_role",
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # Create company with person contacts
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def create_company_contacts(
        company_name: str,
        contact_names: list[str],
    ):
        """
        Create a company contact if it does not already exist, then create
        person contacts under that company.

        Existing company/person contacts are reused instead of duplicated.

        Example:
        - Create company "MCP Test" if it does not exist, then create:
          Test MCP User 1
          Test MCP User 2
          Test MCP User 3
          Test MCP User 4
          Test MCP User 5

        Provider: Zen Business Solutions
        """

        tool = "create_company_contacts"

        params = {
            "company_name": company_name,
            "contact_names": contact_names,
        }

        try:
            company_name = clean_search(company_name)

            if not company_name:
                raise ValueError(
                    "company_name is required."
                )

            if not contact_names:
                raise ValueError(
                    "At least one contact name is required."
                )

            if len(contact_names) > settings.max_results:
                raise ValueError(
                    f"Too many contacts. Maximum allowed is {settings.max_results}."
                )

            cleaned_contact_names = []

            for index, contact_name in enumerate(
                contact_names,
                start=1,
            ):
                cleaned_name = clean_search(contact_name)

                if not cleaned_name:
                    raise ValueError(
                        f"contact_names[{index}] cannot be empty."
                    )

                if cleaned_name not in cleaned_contact_names:
                    cleaned_contact_names.append(cleaned_name)

            companies = await odoo.search_read(
                model="res.partner",
                domain=[
                    [
                        "name",
                        "=",
                        company_name,
                    ],
                    [
                        "is_company",
                        "=",
                        True,
                    ],
                ],
                fields=[
                    "id",
                    "name",
                    "display_name",
                    "is_company",
                    "active",
                ],
                limit=1,
            )

            company_created = False

            if companies:
                company = companies[0]
                company_id = company["id"]
            else:
                company_id = await odoo.create(
                    model="res.partner",
                    values={
                        "name": company_name,
                        "is_company": True,
                    },
                )

                positive_id(
                    company_id,
                    "created_company_id",
                )

                company_created = True

                company_records = await odoo.read(
                    model="res.partner",
                    record_ids=[company_id],
                    fields=[
                        "id",
                        "name",
                        "display_name",
                            "is_company",
                        "active",
                    ],
                )

                if not company_records:
                    raise ValueError(
                        "Company was created but could not be read back."
                    )

                company = company_records[0]

            created_contacts = []
            existing_contacts = []

            for contact_name in cleaned_contact_names:
                existing = await odoo.search_read(
                    model="res.partner",
                    domain=[
                        [
                            "name",
                            "=",
                            contact_name,
                        ],
                        [
                            "parent_id",
                            "=",
                            company_id,
                        ],
                        [
                            "is_company",
                            "=",
                            False,
                        ],
                    ],
                    fields=[
                        "id",
                        "name",
                        "display_name",
                            "type",
                        "parent_id",
                        "active",
                    ],
                    limit=1,
                )

                if existing:
                    existing_contacts.append(
                        existing[0]
                    )
                    continue

                contact_id = await odoo.create(
                    model="res.partner",
                    values={
                        "name": contact_name,
                        "is_company": False,
                        "type": "contact",
                        "parent_id": company_id,
                    },
                )

                positive_id(
                    contact_id,
                    "created_contact_id",
                )

                contact_records = await odoo.read(
                    model="res.partner",
                    record_ids=[contact_id],
                    fields=[
                        "id",
                        "name",
                        "display_name",
                            "type",
                        "parent_id",
                        "active",
                    ],
                )

                if not contact_records:
                    raise ValueError(
                        f'Contact "{contact_name}" was created '
                        "but could not be read back."
                    )

                created_contacts.append(
                    contact_records[0]
                )

            log_tool(
                tool,
                params,
                success=True,
            )

            return branded_response(
                {
                    "success": True,
                    "company_created": company_created,
                    "company": company,
                    "requested_contact_count": len(
                        cleaned_contact_names
                    ),
                    "created_contact_count": len(
                        created_contacts
                    ),
                    "existing_contact_count": len(
                        existing_contacts
                    ),
                    "created_contacts": created_contacts,
                    "existing_contacts": existing_contacts,
                    "message": (
                        "Company and person contacts processed successfully. "
                        "Existing matching records were not duplicated."
                    ),
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )

    # -----------------------------------------------------------------------
    # Business card import helpers
    # -----------------------------------------------------------------------

    def _card_text(value: Any) -> str:
        """Normalize text extracted by Claude from a business card."""
        if value is None:
            return ""
        return str(value).strip()

    def _normalize_business_card_scan_quality(
        raw_card: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Normalize Claude's visual assessment of one scanned business card.

        IMPORTANT:
        The MCP tool does not receive or inspect image pixels. Claude must inspect
        the uploaded scan before calling this tool and include scan_quality for
        every detected card.

        Cropping by itself does NOT make a card non-importable. A cropped card may
        still be imported when all data required by the existing import workflow
        is readable. The preview/create tools decide final importability after
        validating the extracted person/company information.
        """
        raw_quality = raw_card.get("scan_quality")

        if not isinstance(raw_quality, dict):
            return {
                "status": "unknown",
                "is_cropped": None,
                "is_fully_visible": None,
                "crop_sides": [],
                "confidence": "unknown",
                "warning": (
                    "Scan quality was not supplied. Claude must inspect the uploaded "
                    "image and confirm whether this business card is fully visible "
                    "or cropped."
                ),
                "requires_rescan": True,
                "crop_warning_only": False,
            }

        is_cropped = raw_quality.get("is_cropped")
        is_fully_visible = raw_quality.get("is_fully_visible")

        if not isinstance(is_cropped, bool):
            is_cropped = None
        if not isinstance(is_fully_visible, bool):
            is_fully_visible = None

        raw_crop_sides = raw_quality.get(
            "crop_sides",
            raw_quality.get("cropped_sides", []),
        )
        allowed_sides = {"top", "right", "bottom", "left"}
        crop_sides: list[str] = []

        if isinstance(raw_crop_sides, str):
            raw_crop_sides = [raw_crop_sides]

        if isinstance(raw_crop_sides, (list, tuple, set)):
            for side in raw_crop_sides:
                normalized_side = _card_text(side).lower()
                if (
                    normalized_side in allowed_sides
                    and normalized_side not in crop_sides
                ):
                    crop_sides.append(normalized_side)

        confidence = _card_text(raw_quality.get("confidence")).lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "unknown"

        warning = _card_text(
            raw_quality.get("warning")
            or raw_quality.get("reason")
        )

        if is_cropped is True or is_fully_visible is False:
            status = "cropped"

            # IMPORTANT:
            # A crop is now only a warning. It is NOT an automatic rescan/block.
            # The preview/create tools determine whether required import data is
            # actually missing after they inspect the extracted card fields.
            requires_rescan = False
            crop_warning_only = True

            if not warning:
                sides_text = ", ".join(crop_sides)
                warning = (
                    f"Business card has cropped areas on: {sides_text}. "
                    "The card may still be imported if all required data is readable."
                    if sides_text
                    else (
                        "Business card has cropped areas. The card may still be "
                        "imported if all required data is readable."
                    )
                )

        elif is_cropped is False and is_fully_visible is True:
            status = "complete"
            requires_rescan = False
            crop_warning_only = False
            if not warning:
                warning = ""
        else:
            status = "unknown"
            requires_rescan = True
            crop_warning_only = False
            if not warning:
                warning = (
                    "Claude could not confirm whether the business card is complete "
                    "or cropped. Review the scan before importing it."
                )

        return {
            "status": status,
            "is_cropped": is_cropped,
            "is_fully_visible": is_fully_visible,
            "crop_sides": crop_sides,
            "confidence": confidence,
            "warning": warning or None,
            "requires_rescan": requires_rescan,
            "crop_warning_only": crop_warning_only,
        }

    def _email_domain(email: str) -> str:
        """Return the normalized domain from an email address."""
        email = _card_text(email).lower()
        if "@" not in email:
            return ""
        local_part, domain = email.rsplit("@", 1)
        if not local_part or not domain or "." not in domain:
            return ""
        return domain.strip().strip(".")

    def _company_name_from_email_domain(email: str) -> str:
        """Infer a conservative company name from a non-public email domain."""
        domain = _email_domain(email)
        if not domain:
            return ""

        public_domains = {
            "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk",
            "outlook.com", "hotmail.com", "live.com", "msn.com",
            "icloud.com", "me.com", "mac.com", "aol.com",
            "proton.me", "protonmail.com", "gmx.com", "mail.com",
            "zoho.com",
        }
        if domain in public_domains:
            return ""

        parts = [part for part in domain.split(".") if part]
        if len(parts) < 2:
            return ""

        common_second_level_suffixes = {
            "co", "com", "org", "net", "gov", "edu", "ac",
        }
        if (
            len(parts) >= 3
            and len(parts[-1]) == 2
            and parts[-2] in common_second_level_suffixes
        ):
            company_part = parts[-3]
        else:
            company_part = parts[-2]

        company_part = company_part.replace("-", " ").replace("_", " ").strip()
        if not company_part:
            return ""
        return " ".join(word.capitalize() for word in company_part.split())

    async def _find_company_by_name(name: str) -> list[dict[str, Any]]:
        """Find company partners using res.partner.name + is_company=True."""
        name = _card_text(name)
        if not name:
            return []
        return await odoo.search_read(
            model="res.partner",
            domain=[
                ["name", "=ilike", name],
                ["is_company", "=", True],
            ],
            fields=[
                "id", "name", "display_name", "is_company", "email",
                "phone", "mobile_no", "website", "street", "street2",
                "city", "state_id", "zip", "country_id", "active",
            ],
            limit=10,
            order="id asc",
        )

    async def _find_company_by_email_domain(email: str) -> list[dict[str, Any]]:
        """Find existing company partners by company email/website domain."""
        domain = _email_domain(email)
        if not domain:
            return []
        return await odoo.search_read(
            model="res.partner",
            domain=[
                ["is_company", "=", True],
                "|",
                ["email", "ilike", f"@{domain}"],
                ["website", "ilike", domain],
            ],
            fields=[
                "id", "name", "display_name", "is_company", "email",
                "phone", "mobile_no", "website", "street", "street2",
                "city", "state_id", "zip", "country_id", "active",
            ],
            limit=10,
            order="id asc",
        )

    async def _resolve_company_for_business_card(
        company: dict[str, Any] | None,
        person_email: str,
    ) -> dict[str, Any]:
        """
        Resolve the company for one business card.

        There is no company_name field.
        Company records use name + is_company=True.
        Person records use name + is_company=False.
        """
        company = company if isinstance(company, dict) else {}
        visible_company_name = _card_text(company.get("name"))
        email_domain = _email_domain(person_email)

        if visible_company_name:
            companies = await _find_company_by_name(visible_company_name)
            return {
                "status": "ambiguous" if len(companies) > 1 else "resolved",
                "name": visible_company_name,
                "name_source": "business_card",
                "companies": companies,
                "email_domain": email_domain or None,
            }

        if not email_domain:
            return {
                "status": "missing",
                "name": "",
                "name_source": None,
                "companies": [],
                "email_domain": None,
            }

        companies = await _find_company_by_email_domain(person_email)
        if len(companies) > 1:
            return {
                "status": "ambiguous",
                "name": "",
                "name_source": "email_domain_match",
                "companies": companies,
                "email_domain": email_domain,
            }
        if len(companies) == 1:
            return {
                "status": "resolved",
                "name": _card_text(companies[0].get("name")),
                "name_source": "email_domain_match",
                "companies": companies,
                "email_domain": email_domain,
            }

        inferred_name = _company_name_from_email_domain(person_email)
        if inferred_name:
            return {
                "status": "resolved",
                "name": inferred_name,
                "name_source": "email_domain_inferred",
                "companies": [],
                "email_domain": email_domain,
            }

        return {
            "status": "missing",
            "name": "",
            "name_source": None,
            "companies": [],
            "email_domain": email_domain,
        }

    async def _find_existing_person(
        name: str,
        company_id: int,
        email: str = "",
    ) -> dict[str, Any] | None:
        """Check person duplicates by email first, then name + parent company."""
        if email:
            records = await odoo.search_read(
                model="res.partner",
                domain=[
                    ["email", "=ilike", email],
                    ["is_company", "=", False],
                ],
                fields=[
                    "id", "name", "display_name", "email", "phone",
                    "mobile_no", "function", "parent_id", "is_company", "active",
                ],
                limit=1,
            )
            if records:
                return records[0]

        records = await odoo.search_read(
            model="res.partner",
            domain=[
                ["name", "=ilike", name],
                ["parent_id", "=", company_id],
                ["is_company", "=", False],
            ],
            fields=[
                "id", "name", "display_name", "email", "phone",
                "mobile_no", "function", "parent_id", "is_company", "active",
            ],
            limit=1,
        )
        return records[0] if records else None

    RECORD_SOURCE_FIELD = "record_source"

    async def _attach_record_source_to_partner(
        partner_id: int,
        record_source: str,
    ) -> dict[str, Any]:
        """
        Persist the business-card record source directly on res.partner.

        IMPORTANT:
        - Record Source is stored in the dedicated res.partner field:
              record_source
        - No Contact Tag is created.
        - Existing Contact Tags are left untouched.
        - Notes/comment are left untouched.
        - This helper is used for existing/reused companies and people.
        """
        partner_id = positive_id(
            partner_id,
            "partner_id",
        )
        record_source = _card_text(
            record_source
        )

        if not record_source:
            raise ValueError(
                "record_source is required."
            )

        await odoo.execute(
            "res.partner",
            "write",
            [partner_id],
            vals={
                RECORD_SOURCE_FIELD: record_source,
            },
        )

        updated = await odoo.read(
            model="res.partner",
            record_ids=[partner_id],
            fields=[
                "id",
                "name",
                RECORD_SOURCE_FIELD,
            ],
        )

        if not updated:
            raise ValueError(
                f"Partner ID {partner_id} was updated but could not be read back."
            )

        return updated[0]

    async def _resolve_country_id(country_name: str) -> int | None:
        if not country_name:
            return None
        countries = await odoo.search_read(
            model="res.country",
            domain=[["name", "=ilike", country_name]],
            fields=["id", "name", "code"],
            limit=1,
        )
        return countries[0]["id"] if countries else None

    async def _resolve_state_id(
        state_name: str,
        country_id: int | None,
    ) -> int | None:
        if not state_name:
            return None
        domain: list[Any] = [["name", "=ilike", state_name]]
        if country_id:
            domain.append(["country_id", "=", country_id])
        states = await odoo.search_read(
            model="res.country.state",
            domain=domain,
            fields=["id", "name", "code", "country_id"],
            limit=1,
        )
        return states[0]["id"] if states else None

    def _odoo_base_url() -> str:
        """
        Resolve the configured Odoo base URL without assuming one exact
        Settings/OdooClient attribute name.
        """
        candidates = [
            getattr(settings, "odoo_url", None),
            getattr(settings, "ODOO_URL", None),
            getattr(odoo, "base_url", None),
            getattr(odoo, "odoo_url", None),
            getattr(odoo, "url", None),
        ]

        for candidate in candidates:
            value = _card_text(candidate)

            if not value:
                continue

            value = value.rstrip("/")

            # Be defensive in case a JSON-2 endpoint was configured instead
            # of the root Odoo URL.
            for suffix in (
                "/json/2",
                "/json",
            ):
                if value.endswith(suffix):
                    value = value[:-len(suffix)].rstrip("/")

            return value

        return ""

    def _odoo_contact_url(
        partner_id: int | None,
    ) -> str | None:
        """
        Build a direct Odoo Contacts URL for a res.partner record.

        Companies and people are both res.partner records, so they use the
        same Contacts route.
        """
        if not partner_id:
            return None

        base_url = _odoo_base_url()

        if not base_url:
            return None

        return (
            f"{base_url}/odoo/contacts/"
            f"{positive_id(partner_id, 'partner_id')}"
        )

    @mcp.tool()
    async def preview_business_card_contacts(
        record_source: str,
        cards: list[dict[str, Any]],
    ):
        """
        Preview business-card records extracted from uploaded scans by Claude.

        Claude MUST visually inspect every detected business card before calling
        this tool and include a scan_quality object for every card. A card touching
        or extending outside the scan/image boundary must be marked as cropped.

        Card structure:
        {
            "scan_quality": {
                "is_cropped": False,
                "is_fully_visible": True,
                "crop_sides": [],
                "confidence": "high",
                "warning": ""
            },
            "person": {
                "name": "Joe Nagy",
                "is_company": False,
                "job_title": "Sales",
                "email": "jnagy@peltierpro.com",
                "phone": "...",
                "mobile_no": "...",
                "website": "...",
                "street": "...",
                "street2": "...",
                "city": "...",
                "state": "...",
                "zip": "...",
                "country": "..."
            },
            "company": {
                "name": "Peltier",
                "is_company": True,
                "website": "peltierpro.com"
            }
        }

        Cropped-card example:
        {
            "scan_quality": {
                "is_cropped": True,
                "is_fully_visible": False,
                "crop_sides": ["right"],
                "confidence": "high",
                "warning": "The right side of the card is outside the scan."
            },
            "person": {
                "name": "John Smith"
            }
        }

        The company object may be omitted when no company name is visible.
        In that case, the person's email domain is used to search/infer company.

        IMPORTANT:
        - scan_quality is required so Claude can report scan/crop conditions.
        - Cropping alone does not block import.
        - A cropped card is importable when the existing required import data is
          readable: person name plus a resolvable company.
        - A cropped card that is missing required import data requires rescanning.
        - Unknown/missing scan quality receives status=scan_quality_unknown.
        - There is no company_name field.
        - Company = name + is_company=True.
        - Person = name + is_company=False.
        - mobile_no is the Claude/MCP input key; it maps directly to Odoo res.partner.mobile_no.
        - This tool never creates records.
        - Explicit confirmation is required before create_business_card_contacts.
        - No CRM lead/opportunity is created.
        """
        tool = "preview_business_card_contacts"
        params = {
            "record_source": record_source,
            "card_count": len(cards) if cards else 0,
        }

        try:
            record_source = _card_text(record_source)
            if not record_source:
                raise ValueError("record_source is required before processing business cards.")
            if not cards:
                raise ValueError("At least one business card is required.")
            if len(cards) > settings.max_results:
                raise ValueError(
                    f"Too many business cards. Maximum allowed is {settings.max_results}."
                )

            preview = []
            for index, raw_card in enumerate(cards, start=1):
                if not isinstance(raw_card, dict):
                    preview.append({
                        "index": index,
                        "status": "invalid",
                        "requires_rescan": False,
                        "can_import": False,
                        "error": "Business card must be an object/dictionary.",
                    })
                    continue

                person = raw_card.get("person")
                company = raw_card.get("company")
                scan_quality = _normalize_business_card_scan_quality(raw_card)

                if scan_quality["requires_rescan"]:
                    visible_person = person if isinstance(person, dict) else {}
                    visible_company = company if isinstance(company, dict) else {}
                    scan_status = scan_quality["status"]
                    preview.append({
                        "index": index,
                        "status": (
                            "cropped_card"
                            if scan_status == "cropped"
                            else "scan_quality_unknown"
                        ),
                        "requires_rescan": True,
                        "can_import": False,
                        "scan_quality": scan_quality,
                        "person": {
                            "name": _card_text(visible_person.get("name")) or None,
                            "email": _card_text(visible_person.get("email")) or None,
                            "phone": _card_text(visible_person.get("phone")) or None,
                            "mobile_no": _card_text(visible_person.get("mobile_no")) or None,
                        },
                        "company": (
                            {
                                "name": _card_text(visible_company.get("name")) or None,
                                "is_company": True,
                            }
                            if visible_company
                            else None
                        ),
                        "error": scan_quality["warning"],
                    })
                    continue

                if not isinstance(person, dict):
                    is_cropped = scan_quality["status"] == "cropped"
                    preview.append({
                        "index": index,
                        "status": (
                            "cropped_missing_required_data"
                            if is_cropped
                            else "invalid"
                        ),
                        "requires_rescan": is_cropped,
                        "can_import": False,
                        "scan_quality": scan_quality,
                        "error": (
                            "The card has cropped areas and the required person "
                            "information could not be read. Please rescan this card."
                            if is_cropped
                            else "Business card must contain a person object."
                        ),
                    })
                    continue

                person_name = _card_text(person.get("name"))
                email = _card_text(person.get("email"))
                mobile_no = _card_text(person.get("mobile_no"))
                if not person_name:
                    is_cropped = scan_quality["status"] == "cropped"
                    preview.append({
                        "index": index,
                        "status": (
                            "cropped_missing_required_data"
                            if is_cropped
                            else "invalid"
                        ),
                        "requires_rescan": is_cropped,
                        "can_import": False,
                        "scan_quality": scan_quality,
                        "error": (
                            "The card has cropped areas and the person's name is not "
                            "readable. The missing required data prevents import; "
                            "please rescan this card."
                            if is_cropped
                            else "Business card has no readable person name."
                        ),
                    })
                    continue

                resolution = await _resolve_company_for_business_card(company, email)
                resolved_name = _card_text(resolution.get("name"))
                companies = resolution.get("companies") or []
                name_source = resolution.get("name_source")
                email_domain = resolution.get("email_domain")

                if resolution.get("status") == "missing":
                    is_cropped = scan_quality["status"] == "cropped"
                    preview.append({
                        "index": index,
                        "status": (
                            "cropped_missing_required_data"
                            if is_cropped
                            else "needs_company"
                        ),
                        "requires_rescan": is_cropped,
                        "can_import": False,
                        "scan_quality": scan_quality,
                        "person": {
                            "name": person_name,
                            "is_company": False,
                            "email": email or None,
                            "mobile_no": mobile_no or None,
                        },
                        "company": None,
                        "email_domain": email_domain,
                        "error": (
                            "The card has cropped areas and no company name or usable "
                            "company email domain could be read. Required import data "
                            "is missing, so please rescan this card."
                            if is_cropped
                            else (
                                "No company name is visible and the email could not safely "
                                "identify a company. Ask the user for the company before import."
                            )
                        ),
                    })
                    continue

                if resolution.get("status") == "ambiguous":
                    preview.append({
                        "index": index,
                        "status": "ambiguous_company",
                        "requires_rescan": False,
                        "can_import": False,
                        "scan_quality": scan_quality,
                        "person": {
                            "name": person_name,
                            "is_company": False,
                            "email": email or None,
                            "mobile_no": mobile_no or None,
                        },
                        "company": {
                            "name": resolved_name or None,
                            "is_company": True,
                            "name_source": name_source,
                        },
                        "company_matches": companies,
                        "email_domain": email_domain,
                        "error": (
                            "Multiple Odoo companies matched. Ask the user to identify "
                            "the correct company; do not guess."
                        ),
                    })
                    continue

                if companies:
                    company_record = companies[0]
                    company_id = positive_id(company_record["id"], "company_id")
                    existing_person = await _find_existing_person(
                        name=person_name,
                        company_id=company_id,
                        email=email,
                    )
                    preview.append({
                        "index": index,
                        "status": (
                            "existing_person" if existing_person
                            else "ready_existing_company"
                        ),
                        "requires_rescan": False,
                        "can_import": True,
                        "cropped_but_usable": (
                            scan_quality["status"] == "cropped"
                        ),
                        "scan_quality": scan_quality,
                        "notice": (
                            f"Card #{index} has cropped areas, but the required "
                            "information for import is already readable. This card "
                            "is still okay to upload after confirmation."
                            if scan_quality["status"] == "cropped"
                            else None
                        ),
                        "person": {
                            "name": person_name,
                            "is_company": False,
                            "email": email or None,
                            "mobile_no": mobile_no or None,
                        },
                        "company": {
                            "id": company_id,
                            "name": company_record.get("name"),
                            "is_company": True,
                            "name_source": name_source,
                            "url": _odoo_contact_url(company_id),
                        },
                        "email_domain": email_domain,
                        "company_exists": True,
                        "person_exists": bool(existing_person),
                        "existing_person": (
                            {
                                **existing_person,
                                "url": _odoo_contact_url(
                                    existing_person.get("id")
                                ),
                            }
                            if existing_person
                            else None
                        ),
                    })
                else:
                    preview.append({
                        "index": index,
                        "status": "ready_new_company",
                        "requires_rescan": False,
                        "can_import": True,
                        "cropped_but_usable": (
                            scan_quality["status"] == "cropped"
                        ),
                        "scan_quality": scan_quality,
                        "notice": (
                            f"Card #{index} has cropped areas, but the required "
                            "information for import is already readable. This card "
                            "is still okay to upload after confirmation."
                            if scan_quality["status"] == "cropped"
                            else None
                        ),
                        "person": {
                            "name": person_name,
                            "is_company": False,
                            "email": email or None,
                            "mobile_no": mobile_no or None,
                        },
                        "company": {
                            "name": resolved_name,
                            "is_company": True,
                            "name_source": name_source,
                        },
                        "email_domain": email_domain,
                        "company_exists": False,
                        "person_exists": False,
                    })

            cropped_but_usable_count = sum(
                1 for item in preview
                if item.get("cropped_but_usable") is True
            )
            cropped_missing_required_data_count = sum(
                1 for item in preview
                if item.get("status") == "cropped_missing_required_data"
            )
            scan_quality_unknown_count = sum(
                1 for item in preview
                if item.get("status") == "scan_quality_unknown"
            )
            requires_rescan_count = sum(
                1 for item in preview
                if item.get("requires_rescan") is True
            )
            importable_card_count = sum(
                1 for item in preview
                if item.get("can_import") is True
            )

            log_tool(tool, params, success=True)
            return branded_response({
                "success": True,
                "record_source": record_source,
                "count": len(preview),
                "cropped_but_usable_count": cropped_but_usable_count,
                "cropped_missing_required_data_count": (
                    cropped_missing_required_data_count
                ),
                "scan_quality_unknown_count": scan_quality_unknown_count,
                "requires_rescan_count": requires_rescan_count,
                "importable_card_count": importable_card_count,
                "preview": preview,
                "write_performed": False,
                "requires_confirmation": True,
                "message": (
                    "Preview completed. No Odoo records were created. "
                    + (
                        f"{cropped_but_usable_count} card(s) have cropped areas, but "
                        "the required information for import is already readable; "
                        "they remain importable after confirmation. "
                        if cropped_but_usable_count
                        else ""
                    )
                    + (
                        f"{requires_rescan_count} card(s) still require review/rescanning "
                        "because required data or scan-quality confirmation is missing. "
                        if requires_rescan_count
                        else ""
                    )
                    + (
                        "Obtain explicit user confirmation before calling "
                        "create_business_card_contacts with user_confirmed=true."
                    )
                ),
            })
        except Exception as exc:
            return failed(tool, exc, params)

    @mcp.tool()
    async def create_business_card_contacts(
        record_source: str,
        cards: list[dict[str, Any]],
        user_confirmed: bool = False,
    ):
        """
        Create Odoo Contacts from business cards uploaded to Claude.

        HARD RULES:
        - user_confirmed must be True or nothing is created.
        - Claude must provide scan_quality for every detected business card.
        - Cropping alone does not block creation.
        - A cropped card may be imported when the person name and company can be
          resolved from the visible extracted data.
        - A cropped card is skipped only when required import data is missing.
        - Cards with missing/uncertain scan quality are still skipped for review.
        - There is no company_name field.
        - Company uses name + is_company=True.
        - Person uses name + is_company=False.
        - Person is linked to company through parent_id.
        - mobile_no maps directly to Odoo's res.partner.mobile_no field.
        - If company.name is missing, search/infer company using person email domain.
        - Existing companies/people are reused.
        - Record source is persisted for BOTH newly created and existing/reused
          partners directly in the dedicated res.partner record_source field.
        - No Contact Tag is created for the record source.
        - Created and existing company/person results include direct Odoo Contacts URLs.
        - No crm.lead or opportunity is created.
        """
        tool = "create_business_card_contacts"
        params = {
            "record_source": record_source,
            "card_count": len(cards) if cards else 0,
            "user_confirmed": user_confirmed,
        }

        try:
            record_source = _card_text(record_source)
            if not record_source:
                raise ValueError("record_source is required before business-card import.")
            if user_confirmed is not True:
                return branded_response({
                    "success": False,
                    "created": False,
                    "requires_confirmation": True,
                    "message": (
                        "No records were created. Show the preview and obtain explicit "
                        "user confirmation, then call again with user_confirmed=true."
                    ),
                })
            if not cards:
                raise ValueError("At least one business card is required.")
            if len(cards) > settings.max_results:
                raise ValueError(
                    f"Too many business cards. Maximum allowed is {settings.max_results}."
                )

            created_companies = []
            reused_companies = []
            created_contacts = []
            existing_contacts = []
            skipped_contacts = []
            cropped_cards = []
            cropped_but_usable_cards = []
            scan_quality_unknown_cards = []
            company_cache: dict[str, dict[str, Any]] = {}

            for index, raw_card in enumerate(cards, start=1):
                if not isinstance(raw_card, dict):
                    skipped_contacts.append({
                        "index": index,
                        "reason": "Business card must be an object/dictionary.",
                    })
                    continue

                person = raw_card.get("person")
                company = raw_card.get("company")
                scan_quality = _normalize_business_card_scan_quality(raw_card)

                if scan_quality["requires_rescan"]:
                    visible_person = person if isinstance(person, dict) else {}
                    visible_company = company if isinstance(company, dict) else {}
                    skipped_item = {
                        "index": index,
                        "status": (
                            "cropped_card"
                            if scan_quality["status"] == "cropped"
                            else "scan_quality_unknown"
                        ),
                        "requires_rescan": True,
                        "scan_quality": scan_quality,
                        "person": {
                            "name": _card_text(visible_person.get("name")) or None,
                            "email": _card_text(visible_person.get("email")) or None,
                        },
                        "company": (
                            {
                                "name": _card_text(visible_company.get("name")) or None,
                                "is_company": True,
                            }
                            if visible_company
                            else None
                        ),
                        "reason": (
                            scan_quality["warning"]
                            or "Card requires review/rescanning before import."
                        ),
                    }
                    skipped_contacts.append(skipped_item)
                    if scan_quality["status"] == "cropped":
                        cropped_cards.append(skipped_item)
                    else:
                        scan_quality_unknown_cards.append(skipped_item)
                    continue

                if not isinstance(person, dict):
                    is_cropped = scan_quality["status"] == "cropped"
                    skipped_item = {
                        "index": index,
                        "status": (
                            "cropped_missing_required_data"
                            if is_cropped
                            else "invalid"
                        ),
                        "requires_rescan": is_cropped,
                        "scan_quality": scan_quality,
                        "reason": (
                            "The card has cropped areas and the required person "
                            "information could not be read. Contact was not created; "
                            "please rescan this card."
                            if is_cropped
                            else "Business card must contain a person object."
                        ),
                    }
                    skipped_contacts.append(skipped_item)
                    if is_cropped:
                        cropped_cards.append(skipped_item)
                    continue

                person_name = _card_text(person.get("name"))
                job_title = _card_text(person.get("job_title"))
                email = _card_text(person.get("email"))
                phone = _card_text(person.get("phone"))
                mobile_no = _card_text(person.get("mobile_no"))
                website = _card_text(person.get("website"))
                street = _card_text(person.get("street"))
                street2 = _card_text(person.get("street2"))
                city = _card_text(person.get("city"))
                state = _card_text(person.get("state"))
                zip_code = _card_text(person.get("zip"))
                country = _card_text(person.get("country"))

                if not person_name:
                    is_cropped = scan_quality["status"] == "cropped"
                    skipped_item = {
                        "index": index,
                        "status": (
                            "cropped_missing_required_data"
                            if is_cropped
                            else "invalid"
                        ),
                        "requires_rescan": is_cropped,
                        "scan_quality": scan_quality,
                        "reason": (
                            "The card has cropped areas and the person's name is not "
                            "readable. Contact was not created; please rescan this card."
                            if is_cropped
                            else "Business card has no readable person name."
                        ),
                    }
                    skipped_contacts.append(skipped_item)
                    if is_cropped:
                        cropped_cards.append(skipped_item)
                    continue

                resolution = await _resolve_company_for_business_card(company, email)
                resolved_name = _card_text(resolution.get("name"))
                companies = resolution.get("companies") or []
                name_source = resolution.get("name_source")
                email_domain = resolution.get("email_domain")

                if resolution.get("status") == "missing":
                    is_cropped = scan_quality["status"] == "cropped"
                    skipped_item = {
                        "index": index,
                        "status": (
                            "cropped_missing_required_data"
                            if is_cropped
                            else "needs_company"
                        ),
                        "requires_rescan": is_cropped,
                        "scan_quality": scan_quality,
                        "person": {"name": person_name, "is_company": False},
                        "email": email or None,
                        "email_domain": email_domain,
                        "reason": (
                            "The card has cropped areas and no company name or usable "
                            "company email domain could be read. Required import data "
                            "is missing, so the contact was not created; please rescan "
                            "this card."
                            if is_cropped
                            else (
                                "No company name is visible and the email could not safely "
                                "identify a company. Contact was not created."
                            )
                        ),
                    }
                    skipped_contacts.append(skipped_item)
                    if is_cropped:
                        cropped_cards.append(skipped_item)
                    continue

                if resolution.get("status") == "ambiguous":
                    skipped_contacts.append({
                        "index": index,
                        "person": {"name": person_name, "is_company": False},
                        "company_matches": companies,
                        "reason": (
                            "Multiple matching Odoo companies were found. Card was "
                            "skipped to avoid linking to the wrong company."
                        ),
                    })
                    continue

                if scan_quality["status"] == "cropped":
                    cropped_but_usable_cards.append({
                        "index": index,
                        "status": "cropped_but_usable",
                        "can_import": True,
                        "requires_rescan": False,
                        "scan_quality": scan_quality,
                        "person": {
                            "name": person_name,
                            "email": email or None,
                            "phone": phone or None,
                            "mobile_no": mobile_no or None,
                        },
                        "company": {
                            "name": resolved_name,
                            "is_company": True,
                            "name_source": name_source,
                        },
                        "notice": (
                            f"Card #{index} has cropped areas, but the required "
                            "information for import is already readable. The card "
                            "is acceptable for upload and will be processed because "
                            "the user already confirmed the import."
                        ),
                    })

                cache_key = resolved_name.casefold()
                company_record = company_cache.get(cache_key)
                company_was_created = False

                if company_record is None:
                    if companies:
                        company_record = companies[0]

                        company_source_record = await _attach_record_source_to_partner(
                            partner_id=company_record["id"],
                            record_source=record_source,
                        )

                        reused_companies.append({
                            "card_index": index,
                            "record_source": record_source,
                            "name_source": name_source,
                            "email_domain": email_domain,
                            "company": {
                                **company_record,
                                "record_source": company_source_record.get(
                                    RECORD_SOURCE_FIELD
                                ),
                                "url": _odoo_contact_url(
                                    company_record.get("id")
                                ),
                            },
                        })
                    else:
                        company_input = company if isinstance(company, dict) else {}
                        company_website = _card_text(company_input.get("website")) or website
                        company_phone = _card_text(company_input.get("phone"))
                        company_mobile_no = _card_text(company_input.get("mobile_no"))
                        company_street = _card_text(company_input.get("street")) or street
                        company_street2 = _card_text(company_input.get("street2")) or street2
                        company_city = _card_text(company_input.get("city")) or city
                        company_state = _card_text(company_input.get("state")) or state
                        company_zip = _card_text(company_input.get("zip")) or zip_code
                        company_country = _card_text(company_input.get("country")) or country

                        country_id = await _resolve_country_id(company_country)
                        state_id = await _resolve_state_id(company_state, country_id)

                        company_values: dict[str, Any] = {
                            "name": resolved_name,
                            "is_company": True,
                            "type": "contact",
                            RECORD_SOURCE_FIELD: record_source,
                        }
                        if company_website:
                            company_values["website"] = company_website
                        if company_phone:
                            company_values["phone"] = company_phone
                        if company_mobile_no:
                            company_values["mobile_no"] = company_mobile_no
                        if company_street:
                            company_values["street"] = company_street
                        if company_street2:
                            company_values["street2"] = company_street2
                        if company_city:
                            company_values["city"] = company_city
                        if company_zip:
                            company_values["zip"] = company_zip
                        if country_id:
                            company_values["country_id"] = country_id
                        if state_id:
                            company_values["state_id"] = state_id

                        company_id = await odoo.create(
                            model="res.partner",
                            values=company_values,
                        )
                        positive_id(company_id, "created_company_id")
                        company_records = await odoo.read(
                            model="res.partner",
                            record_ids=[company_id],
                            fields=[
                                "id", "name", "display_name", "is_company", "website",
                                "phone", "mobile_no", "street", "street2", "city",
                                "state_id", "zip", "country_id", RECORD_SOURCE_FIELD, "active",
                            ],
                        )
                        if not company_records:
                            raise ValueError(
                                f'Company "{resolved_name}" was created but could not be read back.'
                            )
                        company_record = company_records[0]
                        company_was_created = True
                        created_companies.append({
                            "card_index": index,
                            "record_source": record_source,
                            "name_source": name_source,
                            "email_domain": email_domain,
                            "company": {
                                **company_record,
                                "url": _odoo_contact_url(
                                    company_record.get("id")
                                ),
                            },
                        })

                    company_cache[cache_key] = company_record

                company_id = positive_id(company_record["id"], "company_id")
                existing_person = await _find_existing_person(
                    name=person_name,
                    company_id=company_id,
                    email=email,
                )
                if existing_person:
                    existing_person_source_record = await _attach_record_source_to_partner(
                        partner_id=existing_person["id"],
                        record_source=record_source,
                    )

                    existing_contacts.append({
                        "card_index": index,
                        "record_source": record_source,
                                "company": {
                            "id": company_id,
                            "name": company_record.get("name"),
                            "is_company": True,
                            "url": _odoo_contact_url(company_id),
                        },
                        "contact": {
                            **existing_person,
                            "record_source": existing_person_source_record.get(
                                RECORD_SOURCE_FIELD
                            ),
                            "url": _odoo_contact_url(
                                existing_person.get("id")
                            ),
                        },
                        "reason": (
                            "Matching person contact already exists. "
                            "Record Source field was updated on the existing contact."
                        ),
                    })
                    continue

                country_id = await _resolve_country_id(country)
                state_id = await _resolve_state_id(state, country_id)

                person_values: dict[str, Any] = {
                    "name": person_name,
                    "is_company": False,
                    "type": "contact",
                    "parent_id": company_id,
                    RECORD_SOURCE_FIELD: record_source,
                }
                if job_title:
                    person_values["function"] = job_title
                if email:
                    person_values["email"] = email
                if phone:
                    person_values["phone"] = phone
                if mobile_no:
                    person_values["mobile_no"] = mobile_no
                if website:
                    person_values["website"] = website
                if street:
                    person_values["street"] = street
                if street2:
                    person_values["street2"] = street2
                if city:
                    person_values["city"] = city
                if zip_code:
                    person_values["zip"] = zip_code
                if country_id:
                    person_values["country_id"] = country_id
                if state_id:
                    person_values["state_id"] = state_id

                contact_id = await odoo.create(
                    model="res.partner",
                    values=person_values,
                )
                positive_id(contact_id, "created_contact_id")
                contact_records = await odoo.read(
                    model="res.partner",
                    record_ids=[contact_id],
                    fields=[
                        "id", "name", "display_name", "is_company", "type",
                        "parent_id", "email", "phone", "mobile_no", "website",
                        "street", "street2", "city", "state_id", "zip",
                        "country_id", "function", RECORD_SOURCE_FIELD, "active",
                    ],
                )
                if not contact_records:
                    raise ValueError(
                        f'Contact "{person_name}" was created but could not be read back.'
                    )

                created_contacts.append({
                    "card_index": index,
                    "record_source": record_source,
                        "company_created": company_was_created,
                    "company": {
                        "id": company_id,
                        "name": company_record.get("name"),
                        "is_company": True,
                        "name_source": name_source,
                        "url": _odoo_contact_url(company_id),
                    },
                    "contact": {
                        **contact_records[0],
                        "url": _odoo_contact_url(contact_id),
                    },
                })

            summary = {
                "record_source": record_source,
                "cards_processed": len(cards),
                "new_person_contacts": [
                    {
                        "id": item["contact"].get("id"),
                        "name": item["contact"].get("name"),
                        "url": item["contact"].get("url"),
                        "company_id": item["company"].get("id"),
                        "company_name": item["company"].get("name"),
                        "company_url": item["company"].get("url"),
                    }
                    for item in created_contacts
                ],
                "new_company_contacts": [
                    {
                        "id": item["company"].get("id"),
                        "name": item["company"].get("name"),
                        "url": item["company"].get("url"),
                    }
                    for item in created_companies
                ],
                "existing_person_contacts": [
                    {
                        "id": item["contact"].get("id"),
                        "name": item["contact"].get("name"),
                        "url": item["contact"].get("url"),
                        "company_id": item["company"].get("id"),
                        "company_name": item["company"].get("name"),
                        "company_url": item["company"].get("url"),
                    }
                    for item in existing_contacts
                ],
                "reused_company_contacts": [
                    {
                        "id": item["company"].get("id"),
                        "name": item["company"].get("name"),
                        "url": item["company"].get("url"),
                    }
                    for item in reused_companies
                ],
                "skipped_contacts": skipped_contacts,
                "cropped_cards": cropped_cards,
                "cropped_but_usable_cards": cropped_but_usable_cards,
                "scan_quality_unknown_cards": scan_quality_unknown_cards,
            }

            summary["new_person_contact_count"] = len(
                summary["new_person_contacts"]
            )
            summary["new_company_contact_count"] = len(
                summary["new_company_contacts"]
            )
            summary["existing_person_contact_count"] = len(
                summary["existing_person_contacts"]
            )
            summary["reused_company_contact_count"] = len(
                summary["reused_company_contacts"]
            )
            summary["skipped_contact_count"] = len(
                summary["skipped_contacts"]
            )
            summary["cropped_card_count"] = len(
                summary["cropped_cards"]
            )
            summary["cropped_but_usable_count"] = len(
                summary["cropped_but_usable_cards"]
            )
            summary["scan_quality_unknown_count"] = len(
                summary["scan_quality_unknown_cards"]
            )

            log_tool(tool, params, success=True)
            return branded_response({
                "success": True,
                "record_source": record_source,
                "requested_card_count": len(cards),
                "created_company_count": len(created_companies),
                "reused_company_count": len(reused_companies),
                "created_contact_count": len(created_contacts),
                "existing_contact_count": len(existing_contacts),
                "skipped_contact_count": len(skipped_contacts),
                "cropped_card_count": len(cropped_cards),
                "cropped_but_usable_count": len(cropped_but_usable_cards),
                "scan_quality_unknown_count": len(scan_quality_unknown_cards),
                "created_companies": created_companies,
                "reused_companies": reused_companies,
                "created_contacts": created_contacts,
                "existing_contacts": existing_contacts,
                "skipped_contacts": skipped_contacts,
                "cropped_cards": cropped_cards,
                "cropped_but_usable_cards": cropped_but_usable_cards,
                "scan_quality_unknown_cards": scan_quality_unknown_cards,
                "odoo_base_url": _odoo_base_url() or None,
                "summary": summary,
                "message": (
                    "Business-card import completed as Odoo Contacts. The record "
                    "source was saved directly in the dedicated Record Source field "
                    "for newly created and existing/reused records. No record-source "
                    "Contact Tag was created. Companies use name + "
                    "is_company=True; people use name + is_company=False and are "
                    "linked through parent_id. Direct Odoo Contact URLs are included "
                    "for created and existing records. Cropped cards were allowed when "
                    "the required import data was already readable; cropped cards with "
                    "missing required data and cards with unconfirmed scan quality were "
                    "skipped for review/rescanning. No CRM opportunity was created."
                ),
            })
        except Exception as exc:
            return failed(tool, exc, params)
