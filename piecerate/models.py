from django.db import models

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

    class Meta:
        ordering = ["op_code"]
        constraints = [
            models.UniqueConstraint(fields=["style", "op_code"], name="unique_style_opcode"),
        ]

    def __str__(self):
        return f"{self.style} — {self.name}"
