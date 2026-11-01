"""Curated, read-only customer-facing data from the operational database."""
from sqlalchemy import text

from app.database.postgres import get_pg_session


async def get_customer_public_context(query: str, company_id: int) -> tuple[str, list[dict]]:
    """Return only active public promotions or store names for customer questions."""
    normalized = query.casefold()
    asks_promotions = any(term in normalized for term in (
        "promo", "oferta", "desconto", "barato", "preço", "preco",
    ))
    asks_stores = any(term in normalized for term in (
        "loja", "unidade", "endereço", "endereco", "perto", "próxima", "proxima", "melhor",
    ))
    if not (asks_promotions or asks_stores):
        return "", []

    statements = []
    if asks_promotions:
        statements.append((
            "promotions",
            text("SELECT * FROM mottainai.fn_customer_active_promotions(:company_id)"),
        ))
    if asks_stores:
        statements.append((
            "stores",
            text("SELECT * FROM mottainai.fn_customer_public_stores(:company_id)"),
        ))

    context_parts: list[str] = []
    sources: list[dict] = []
    async with get_pg_session() as session:
        await session.execute(text("SET LOCAL ROLE mottainai_customer_api"))
        for source_name, statement in statements:
            rows = (await session.execute(statement, {"company_id": company_id})).mappings().all()
            sources.append({"type": "sql", "ref": f"customer_public.{source_name}", "score": None})
            if source_name == "promotions":
                lines = [
                    "; ".join(
                        f"promoção: {row['promotion_name']}; produto: {row['product_name']}; "
                        f"loja: {row['store_name']}; preço anterior: R${row['original_price']}; "
                        f"preço promocional: R${row['promotional_price']}; "
                        f"desconto: {row['discount_percent']}%; termina: {row['ends_at']}"
                    ) for row in rows
                ]
                context_parts.append("Promoções ativas e aprovadas:\n" + ("\n".join(lines) if lines else "Nenhuma promoção ativa encontrada."))
            else:
                lines = [f"{row['store_name']} (store_id {row['store_id']})" for row in rows]
                context_parts.append("Lojas ativas:\n" + ("\n".join(lines) if lines else "Nenhuma loja ativa encontrada."))

    return "\n\n".join(context_parts), sources
