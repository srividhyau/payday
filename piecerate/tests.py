import re
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from attendance.models import Employee

from .models import PieceRateEntry, RateCardOperation, Style


class RateCardRowStatusTests(TestCase):
    """Rate Card rows are tinted by pieces logged vs. Order Qty."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        self.style = Style.objects.create(name="Test Oct", year=2026, month=10)
        self.employee = Employee.objects.create(code="E1", name="Asha")
        self.next_code = 1

    def add_op(self, name, order_quantity, produced=None, enabled=True):
        op = RateCardOperation.objects.create(
            style=self.style, op_code=self.next_code, name=name,
            order_quantity=order_quantity, is_enabled=enabled,
        )
        self.next_code += 1
        if produced is not None:
            PieceRateEntry.objects.create(
                rate_card_operation=op, employee=self.employee, date=date(2026, 10, 1), quantity=produced,
            )
        return op

    def row_class(self, op):
        html = self.client.get(reverse("piece_rate_rate_card", args=[self.style.id])).content.decode()
        tag = re.search(rf'<tr id="op-{op.id}"[^>]*>', html).group(0)
        match = re.search(r'class="([^"]*)"', tag)
        return match.group(1) if match else ""

    def test_excess_short_and_balanced(self):
        self.assertEqual(self.row_class(self.add_op("Over", 100, 101)), "diff-more")
        self.assertEqual(self.row_class(self.add_op("Under", 100, 99)), "diff-less")
        self.assertEqual(self.row_class(self.add_op("Even", 100, 100)), "diff-balanced")

    def test_no_order_quantity_has_no_status(self):
        self.assertEqual(self.row_class(self.add_op("No target", 0, 50)), "")

    def test_existing_flags_take_priority(self):
        self.assertEqual(self.row_class(self.add_op("Off", 100, 101, enabled=False)), "op-row-disabled")
        self.assertEqual(self.row_class(self.add_op("Nobody", 100)), "no-operators")
