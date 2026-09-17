import secrets

from django.db import models

from attendance.models import Employee

PAY_TYPE_OPERATOR = "operator"
PAY_TYPE_HELPER = "helper"
PAY_TYPE_FINISHING = "finishing"
PAY_TYPE_CHOICES = [
    (PAY_TYPE_OPERATOR, "Operator"),
    (PAY_TYPE_HELPER, "Helper"),
    (PAY_TYPE_FINISHING, "Finishing"),
]


class Style(models.Model):
    """A garment style/product (e.g. L_Pyjama, HNB_CHINO) — the top-level
    grouping a rate card belongs to.

    A template (is_template=True) is a permanent, reusable rate card
    with no month of its own — it's never used for production directly,
    just kept around as a reference. Everything else is a real working
    style scoped to the month it's set up for (like Salary)."""

    name = models.CharField(max_length=100, unique=True)
    is_template = models.BooleanField(default=False)
    year = models.IntegerField(null=True, blank=True)
    month = models.IntegerField(null=True, blank=True)
    # Shown on the operator mobile entry page so operators can pick a
    # style by photo instead of just reading its English code — the
    # Google Translate widget on that page handles translating the
    # name itself, so there's no separate translated-name field.
    image = models.ImageField(upload_to="style_images/", blank=True, null=True)
    # The first day operators may log against this style — defaults to
    # the 1st of its year/month, but a style that actually started
    # partway through (e.g. the 20th) can be set to that real date so
    # the mobile entry page's date picker doesn't offer days before it
    # actually existed.
    start_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Operation(models.Model):
    """Master catalog of sewing operations, shared across styles — built
    by extracting every operation used in last month's style rate cards,
    fixing spelling/abbreviation drift (e.g. ATTCH -> ATTACH), and
    de-duplicating. A Style's rate card (RateCardOperation) will
    eventually reference these instead of retyping names per style."""

    section = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=200, unique=True)
    machine = models.CharField(max_length=50, blank=True)
    rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    pay_type = models.CharField(max_length=10, choices=PAY_TYPE_CHOICES, default=PAY_TYPE_OPERATOR)

    class Meta:
        ordering = ["section", "name"]

    def __str__(self):
        return self.name


class RateCardOperation(models.Model):
    """One line of a Style's rate card — a single sewing operation and
    what it pays per piece. Which of Operator/Helper/Finishing pay a
    piece counts toward depends on who performs it, not just the
    operation, but every operation has one default bucket here."""

    style = models.ForeignKey(Style, on_delete=models.CASCADE, related_name="operations")
    op_code = models.PositiveIntegerField()
    section = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=200)
    machine = models.CharField(max_length=50, blank=True)
    rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    pay_type = models.CharField(max_length=10, choices=PAY_TYPE_CHOICES, default=PAY_TYPE_OPERATOR)
    order_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Total pieces planned for this operation this month — the "
                   "target every operator's daily entries for it should sum to.",
    )

    class Meta:
        ordering = ["op_code"]
        constraints = [
            models.UniqueConstraint(fields=["style", "op_code"], name="unique_style_opcode"),
        ]

    def __str__(self):
        return f"{self.style} — {self.name}"


class PieceRateEntry(models.Model):
    """One operator's piece count for one operation on one day. Multiple
    operators can log against the same RateCardOperation — see the
    Production page, which groups these by operation and compares their
    sum for the month against RateCardOperation.order_quantity."""

    rate_card_operation = models.ForeignKey(RateCardOperation, on_delete=models.CASCADE, related_name="entries")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="piece_rate_entries")
    date = models.DateField()
    quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["rate_card_operation", "employee", "date"], name="unique_entry_per_operator_per_day"
            ),
        ]

    def __str__(self):
        return f"{self.rate_card_operation} — {self.employee} — {self.date}: {self.quantity}"


class OperatorLink(models.Model):
    """A private, unguessable URL that identifies one operator — the
    whole mechanism behind the mobile self-entry page (see
    operator_entry_view). No login: the token in the link IS the
    identity, so a phone with this link bookmarked/added to its home
    screen stays "linked" to this operator indefinitely, with nothing
    to log into or a session that can expire. Revoke access (lost
    phone, employee left) by deleting this row and generating a new one."""

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="piece_rate_link")
    token = models.CharField(max_length=43, unique=True, default=secrets.token_urlsafe, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee} link"
