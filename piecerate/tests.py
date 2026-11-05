import re
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from attendance.models import Employee

from .models import OperatorSummaryApproval, PieceRateEntry, RateCardOperation, Style


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


class FirstUnapprovedMonthTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))

    def approve(self, year, month):
        OperatorSummaryApproval.objects.create(year=year, month=month)

    def test_none_when_nothing_is_approved(self):
        self.assertIsNone(OperatorSummaryApproval.first_unapproved_month())

    @mock.patch("piecerate.models.date", wraps=date)
    def test_month_after_latest_approved(self, mocked):
        mocked.today.return_value = date(2026, 11, 5)
        self.approve(2026, 8)
        self.approve(2026, 9)
        self.assertEqual(OperatorSummaryApproval.first_unapproved_month(), date(2026, 10, 1))

    @mock.patch("piecerate.models.date", wraps=date)
    def test_never_later_than_current_month(self, mocked):
        mocked.today.return_value = date(2026, 11, 5)
        self.approve(2026, 11)
        self.assertEqual(OperatorSummaryApproval.first_unapproved_month(), date(2026, 11, 1))

    @mock.patch("piecerate.models.date", wraps=date)
    def test_rolls_over_the_year(self, mocked):
        mocked.today.return_value = date(2027, 3, 1)
        self.approve(2026, 12)
        self.assertEqual(OperatorSummaryApproval.first_unapproved_month(), date(2027, 1, 1))

    @mock.patch("piecerate.models.date", wraps=date)
    def test_pages_open_on_it_but_a_chosen_month_still_wins(self, mocked):
        mocked.today.return_value = date(2026, 11, 5)
        self.approve(2026, 9)
        url = reverse("piece_rate_operator_summary")
        self.assertContains(self.client.get(url, secure=True), "October 2026")
        self.assertContains(self.client.get(url, {"date": "2026-08-01"}, secure=True), "August 2026")


@mock.patch("django_otp.middleware.is_verified", lambda user: True)
class ApprovedMonthFrozenTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        self.style = Style.objects.create(name="Test Sep", year=2026, month=9)
        self.op = RateCardOperation.objects.create(style=self.style, op_code=1, name="Bottom", rate=1)
        self.employee = Employee.objects.create(code="E1", name="Asha")
        self.approval = OperatorSummaryApproval.objects.create(year=2026, month=9)

    def post_cell(self, quantity=5):
        return self.client.post(reverse("piece_rate_production", args=[self.style.id]), {
            "rate_card_operation_id": self.op.id, "employee_id": self.employee.id,
            "date": "2026-09-02", "quantity": quantity,
        }, secure=True)

    def test_production_cell_is_refused(self):
        self.assertEqual(self.post_cell().status_code, 403)
        self.assertFalse(PieceRateEntry.objects.exists())

    def test_remove_operator_is_refused(self):
        PieceRateEntry.objects.create(rate_card_operation=self.op, employee=self.employee, date=date(2026, 9, 2), quantity=5)
        self.client.post(reverse("piece_rate_production", args=[self.style.id]), {
            "rate_card_operation_id": self.op.id, "employee_id": self.employee.id, "action": "remove_operator",
        }, secure=True)
        self.assertEqual(PieceRateEntry.objects.count(), 1)

    def test_rate_card_edits_are_refused(self):
        self.client.post(reverse("piece_rate_rate_card", args=[self.style.id]), {"action": f"delete_{self.op.id}"}, secure=True)
        self.assertTrue(RateCardOperation.objects.filter(id=self.op.id).exists())

    def test_style_cannot_be_deleted_or_added(self):
        url = reverse("piece_rate")
        self.client.post(url + "?date=2026-09-01", {"action": "delete_style", "style_id": self.style.id}, secure=True)
        self.client.post(url + "?date=2026-09-01", {"action": "create_style", "name": "New"}, secure=True)
        self.client.post(url + "?date=2026-09-01", {"action": f"shift_next_month_{self.style.id}"}, secure=True)
        self.style.refresh_from_db()
        self.assertEqual((self.style.year, self.style.month), (2026, 9))
        self.assertFalse(Style.objects.filter(name="New").exists())

    def test_unapproved_month_stays_open(self):
        october = Style.objects.create(name="Test Oct", year=2026, month=10)
        operation = RateCardOperation.objects.create(style=october, op_code=1, name="Bottom", rate=1)
        response = self.client.post(reverse("piece_rate_production", args=[october.id]), {
            "rate_card_operation_id": operation.id, "employee_id": self.employee.id,
            "date": "2026-10-02", "quantity": 5,
        }, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PieceRateEntry.objects.filter(rate_card_operation=operation).exists())

    def test_unapproving_reopens_the_month(self):
        self.approval.delete()
        self.assertEqual(self.post_cell().status_code, 200)

    def test_approve_control_is_green_only_when_approved(self):
        url = reverse("piece_rate_operator_summary")
        self.assertContains(self.client.get(url, {"date": "2026-09-01"}, secure=True), "approval-badge approved")
        self.assertNotContains(self.client.get(url, {"date": "2026-10-01"}, secure=True), "approval-badge approved")


class SummaryFiltersTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        style = Style.objects.create(name="Test Oct", year=2026, month=10)
        operation = RateCardOperation.objects.create(style=style, op_code=1, name="Bottom", rate=1)
        employee = Employee.objects.create(code="E1", name="Asha")
        PieceRateEntry.objects.create(rate_card_operation=operation, employee=employee, date=date(2026, 10, 2), quantity=5)

    def test_all_three_views_have_the_filters(self):
        for name in ("piece_rate_operator_summary", "piece_rate_style_summary", "piece_rate_management_summary"):
            response = self.client.get(reverse(name), {"date": "2026-10-01"}, secure=True)
            self.assertContains(response, 'id="operationSearch"')
            self.assertContains(response, 'data-style-name="Test Oct"')
            self.assertContains(response, "initSummaryFilters({")


class StyleSummaryGroupingTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        style = Style.objects.create(name="Test Oct", year=2026, month=10)
        self.second = RateCardOperation.objects.create(style=style, op_code=2, name="Aaa Second", rate=2, order_quantity=10)
        self.first = RateCardOperation.objects.create(style=style, op_code=1, name="Zzz First", rate=1, order_quantity=10)
        self.asha = Employee.objects.create(code="E1", name="Asha")
        self.bala = Employee.objects.create(code="E2", name="Bala")
        for operation, employee, quantity in (
            (self.first, self.asha, 4), (self.first, self.bala, 6), (self.second, self.bala, 3),
        ):
            PieceRateEntry.objects.create(rate_card_operation=operation, employee=employee, date=date(2026, 10, 2), quantity=quantity)

    def context(self, **params):
        response = self.client.get(reverse("piece_rate_style_summary"), {"date": "2026-10-01", **params}, secure=True)
        return response

    def test_default_stays_operator_then_operation(self):
        response = self.context()
        self.assertFalse(response.context["group_by_operation"])
        self.assertContains(response, "Style / Operator / Operation")

    def test_operation_grouping_combines_operators(self):
        response = self.context(group="operation")
        self.assertContains(response, "Style / Operation / Operator")
        operations = response.context["style_rows"][0]["operations"]
        self.assertEqual([o["op_name"] for o in operations], ["Zzz First", "Aaa Second"])
        first = operations[0]
        self.assertEqual((first["quantity"], first["amount"], first["diff_class"]), (10, 10, "diff-balanced"))
        self.assertEqual([(e["employee"].name, e["quantity"]) for e in first["operators"]], [("Asha", 4), ("Bala", 6)])
        self.assertEqual(operations[1]["amount"], 6)

    def test_download_follows_the_grouping(self):
        import io

        import openpyxl

        url = reverse("piece_rate_style_summary_download")
        response = self.client.get(url, {"date": "2026-10-01", "group": "operation"}, secure=True)
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        rows = [row for row in sheet.iter_rows(values_only=True) if row[0]]
        names = [row[0].strip() for row in rows]
        self.assertIn("Style / Operation / Operator", names)
        self.assertLess(names.index("Zzz First"), names.index("Asha"))
        self.assertEqual(rows[-1][7], 16)
