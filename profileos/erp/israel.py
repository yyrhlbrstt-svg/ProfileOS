"""What an Israeli invoice has to carry, and how it is printed.

The ledger already knows what is owed. What it does not know is the shape the
Tax Authority requires the paper to be in, and that shape is specific: the
supplier's עוסק מורשה number, the words that name the document type, and —
since the Israel Invoices model came into force — an allocation number
(מספר הקצאה) obtained from the Tax Authority for invoices above the threshold,
without which the customer cannot deduct the input VAT.

That last one is the reason this module exists. A shop that issues a large
invoice without an allocation number finds out weeks later, from a customer
who will not pay it, and has to credit and reissue. The software knows the
amount at the moment the invoice is raised, so it is the right thing to say
so then.

Nothing here files anything with anybody. It records what was obtained, warns
when something required is missing, and prints the document in Hebrew.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from ..core.errors import ProfileOSError


class DocumentKind(StrEnum):
    """The document types a fabricator issues, by their statutory names."""

    QUOTE = "quote"
    ORDER = "order"
    DELIVERY = "delivery"
    INVOICE = "invoice"
    INVOICE_RECEIPT = "invoice_receipt"
    RECEIPT = "receipt"
    CREDIT = "credit"

    @property
    def hebrew(self) -> str:
        return {
            "quote": "הצעת מחיר",
            "order": "הזמנת עבודה",
            "delivery": "תעודת משלוח",
            "invoice": "חשבונית מס",
            "invoice_receipt": "חשבונית מס קבלה",
            "receipt": "קבלה",
            "credit": "חשבונית זיכוי",
        }[self.value]

    @property
    def is_tax_document(self) -> bool:
        """Whether it carries VAT the customer will try to deduct."""
        return self in (
            DocumentKind.INVOICE,
            DocumentKind.INVOICE_RECEIPT,
            DocumentKind.CREDIT,
        )


#: The allocation-number threshold, as a net amount in shekels. It has been
#: stepped down year by year since the model started, so it is a figure the
#: shop confirms against the Tax Authority rather than one to hard-code and
#: forget: what is stored here is the last value entered, with its date.
@dataclass(frozen=True)
class AllocationRule:
    """The threshold above which an invoice needs an allocation number."""

    threshold: float
    from_date: date
    source: str = ""

    @property
    def is_confirmed(self) -> bool:
        return bool(self.source.strip())


#: A starting value, deliberately marked unconfirmed. The shop's bookkeeper
#: sets the real one, and until they do every large invoice is flagged rather
#: than passed.
DEFAULT_ALLOCATION_RULE = AllocationRule(
    threshold=5000.0,
    from_date=date(2025, 1, 1),
    source="",
)


class PaymentTerms(StrEnum):
    """How Israeli customers actually pay."""

    IMMEDIATE = "immediate"
    NET_30 = "net_30"
    EOM_30 = "eom_30"
    EOM_60 = "eom_60"
    EOM_90 = "eom_90"
    EOM_120 = "eom_120"

    @property
    def hebrew(self) -> str:
        return {
            "immediate": "מזומן",
            "net_30": "שוטף ⁦30⁩",
            "eom_30": "שוטף + ⁦30⁩",
            "eom_60": "שוטף + ⁦60⁩",
            "eom_90": "שוטף + ⁦90⁩",
            "eom_120": "שוטף + ⁦120⁩",
        }[self.value]

    def due(self, invoiced: date) -> date:
        """When the money is actually due.

        "שוטף + 60" does not mean sixty days: it means the end of the month of
        invoice, and then sixty days. The difference is up to a month of cash,
        which is why it is calculated rather than approximated.
        """
        from calendar import monthrange
        from datetime import timedelta

        if self is PaymentTerms.IMMEDIATE:
            return invoiced
        if self is PaymentTerms.NET_30:
            return invoiced + timedelta(days=30)
        days = {"eom_30": 30, "eom_60": 60, "eom_90": 90, "eom_120": 120}[self.value]
        end_of_month = invoiced.replace(
            day=monthrange(invoiced.year, invoiced.month)[1]
        )
        return end_of_month + timedelta(days=days)

    @property
    def exceeds_statutory_default(self) -> bool:
        """Whether these terms run past the ordinary statutory expectation.

        Israel's payment-morality law sets default periods for public bodies
        and for suppliers generally; terms well past them are legal when
        agreed but are worth seeing before they are agreed to.
        """
        return self in (PaymentTerms.EOM_90, PaymentTerms.EOM_120)


@dataclass
class TaxIdentity:
    """Who is issuing, as the Tax Authority needs to see them."""

    name: str
    #: עוסק מורשה / ח״פ — the number that makes the invoice deductible.
    vat_number: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    #: Set when the business is a company rather than a licensed dealer.
    company_number: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.name and self.vat_number and self.address)

    def problems(self) -> list[str]:
        missing = []
        if not self.name:
            missing.append("שם העסק")
        if not self.vat_number:
            missing.append("מספר עוסק מורשה")
        if not self.address:
            missing.append("כתובת העסק")
        return missing


@dataclass
class TaxDocument:
    """One document, ready to be printed and to be checked before it is."""

    kind: DocumentKind
    number: str
    issued: date
    issuer: TaxIdentity
    customer_name: str
    customer_vat_number: str = ""
    customer_address: str = ""
    lines: list[dict] = field(default_factory=list)
    net: float = 0.0
    vat_rate: float = 0.18
    terms: PaymentTerms = PaymentTerms.EOM_30
    #: The allocation number obtained from the Tax Authority, where required.
    allocation_number: str = ""
    reference: str = ""
    note: str = ""
    #: What is being reversed, on a credit note.
    credits: str = ""

    @property
    def vat(self) -> float:
        return round(self.net * self.vat_rate, 2)

    @property
    def gross(self) -> float:
        return round(self.net + self.vat, 2)

    @property
    def due(self) -> date:
        return self.terms.due(self.issued)

    def needs_allocation_number(
        self, rule: AllocationRule = DEFAULT_ALLOCATION_RULE
    ) -> bool:
        """Whether this document has to carry an allocation number."""
        return (
            self.kind.is_tax_document
            and self.issued >= rule.from_date
            and self.net >= rule.threshold
        )

    def problems(self, rule: AllocationRule = DEFAULT_ALLOCATION_RULE) -> list[str]:
        """Everything that would make this document a problem once issued."""
        found = [f"חסר {missing}" for missing in self.issuer.problems()]
        if not self.customer_name:
            found.append("חסר שם הלקוח")
        if self.kind.is_tax_document and not self.lines:
            found.append("אין שורות במסמך")
        if self.needs_allocation_number(rule) and not self.allocation_number:
            found.append(
                f"חשבונית מעל ⁦{rule.threshold:,.0f}⁩ ₪ ללא מספר הקצאה — "
                "הלקוח לא יוכל לקזז את המע״מ"
            )
        if self.needs_allocation_number(rule) and not rule.is_confirmed:
            found.append(
                "סף מספר ההקצאה לא אומת מול רשות המסים — יש לעדכן אותו בהגדרות"
            )
        if self.terms.exceeds_statutory_default:
            found.append(
                f"תנאי תשלום {self.terms.hebrew} — ארוכים מהמקובל, ודא שסוכמו בכתב"
            )
        return found

    @property
    def may_be_issued(self) -> bool:
        return not self.problems()

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("סוג המסמך", self.kind.hebrew),
            ("מספר", self.number),
            ("תאריך", f"⁦{self.issued.strftime('%d/%m/%Y')}⁩"),
            ("לפני מע״מ", f"⁦{self.net:,.2f}⁩ ₪"),
            ("מע״מ ⁦{:.0%}⁩".format(self.vat_rate), f"⁦{self.vat:,.2f}⁩ ₪"),
            ("סה״כ לתשלום", f"⁦{self.gross:,.2f}⁩ ₪"),
            ("תנאי תשלום", f"{self.terms.hebrew} · לתשלום עד ⁦{self.due.strftime('%d/%m/%Y')}⁩"),
            ("מספר הקצאה", self.allocation_number or "—"),
        ]


def from_invoice(
    invoice,
    issuer: TaxIdentity,
    *,
    kind: DocumentKind = DocumentKind.INVOICE,
    customer_vat_number: str = "",
    terms: PaymentTerms = PaymentTerms.EOM_30,
    allocation_number: str = "",
) -> TaxDocument:
    """Turn a ledger invoice into the document that gets printed."""
    lines = [
        {
            "description": row["description"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "unit_price": row["unit_price"] / 100.0,
            "discount": row["discount"],
            "net": row["net"] / 100.0,
        }
        for row in invoice.as_rows()
    ]
    return TaxDocument(
        kind=kind,
        number=invoice.invoice_id,
        issued=invoice.on,
        issuer=issuer,
        customer_name=invoice.customer,
        customer_vat_number=customer_vat_number,
        lines=lines,
        net=invoice.net / 100.0,
        vat_rate=invoice.vat_rate,
        terms=terms,
        allocation_number=allocation_number,
        note=invoice.note or "",
        credits=invoice.credits or "",
    )


def render_document(document: TaxDocument) -> str:
    """The document as printable Hebrew HTML, on the shop's own paper."""
    from html import escape

    from ..branding import active_brand
    from ..quoting.document import _document_css

    brand = active_brand()
    rows = []
    for index, line in enumerate(document.lines, start=1):
        rows.append(
            "<tr>"
            f"<td class='n'>{index}</td>"
            f"<td>{escape(str(line.get('description', '')))}</td>"
            f"<td class='n'>{line.get('quantity', 0):,.2f}</td>"
            f"<td>{escape(str(line.get('unit', '')))}</td>"
            f"<td class='n'>{line.get('unit_price', 0):,.2f}</td>"
            f"<td class='n'>{line.get('net', 0):,.2f}</td>"
            "</tr>"
        )

    problems = document.problems()
    banner = ""
    if problems:
        banner = (
            "<div class='warn'><strong>לא להוצאה עדיין:</strong> "
            + " · ".join(escape(problem) for problem in problems)
            + "</div>"
        )

    identity = document.issuer
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>{escape(document.kind.hebrew)} {escape(document.number)}</title>
<style>{_document_css()}</style></head><body><div class="page">
<header>
  <div>
    <h1>{escape(document.kind.hebrew)}</h1>
    <div class="muted">מספר ⁦{escape(document.number)}⁩ · ⁦{document.issued.strftime('%d/%m/%Y')}⁩</div>
  </div>
  <div class="issuer">
    <strong>{escape(identity.name or brand.display_name)}</strong><br>
    {escape(identity.address)}<br>
    {escape(identity.phone)}<br>
    עוסק מורשה ⁦{escape(identity.vat_number)}⁩
  </div>
</header>
{banner}
<section class="parties">
  <div><span class="muted">לכבוד</span><br><strong>{escape(document.customer_name)}</strong><br>
  {escape(document.customer_address)}<br>
  {'ח״פ / עוסק ⁦' + escape(document.customer_vat_number) + '⁩' if document.customer_vat_number else ''}</div>
  <div><span class="muted">אסמכתא</span><br>{escape(document.reference) or '—'}</div>
</section>
<table><thead><tr>
  <th class="n">#</th><th>תיאור</th><th class="n">כמות</th><th>יחידה</th>
  <th class="n">מחיר</th><th class="n">סה״כ</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<table class="totals"><tbody>
  <tr><td>לפני מע״מ</td><td class="n">⁦{document.net:,.2f}⁩ ₪</td></tr>
  <tr><td>מע״מ ⁦{document.vat_rate:.0%}⁩</td><td class="n">⁦{document.vat:,.2f}⁩ ₪</td></tr>
  <tr class="grand"><td>סה״כ לתשלום</td><td class="n">⁦{document.gross:,.2f}⁩ ₪</td></tr>
</tbody></table>
<section class="terms">
  <div>תנאי תשלום: {document.terms.hebrew} · לתשלום עד ⁦{document.due.strftime('%d/%m/%Y')}⁩</div>
  {'<div>מספר הקצאה: ⁦' + escape(document.allocation_number) + '⁩</div>' if document.allocation_number else ''}
  {'<div>' + escape(document.note) + '</div>' if document.note else ''}
  {'<div>מזכה את חשבונית ⁦' + escape(document.credits) + '⁩</div>' if document.credits else ''}
</section>
<footer><span>{escape(brand.document_name)}</span>
<span>{escape(document.kind.hebrew)} ⁦{escape(document.number)}⁩</span></footer>
</div></body></html>"""


__all__ = [
    "DEFAULT_ALLOCATION_RULE",
    "AllocationRule",
    "DocumentKind",
    "PaymentTerms",
    "TaxDocument",
    "TaxIdentity",
    "from_invoice",
    "render_document",
]
