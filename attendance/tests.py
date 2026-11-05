from datetime import date
from unittest import mock

import pandas as pd
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import EmployeeForm
from .importer import import_dataframe
from .models import AttendanceRecord, Department, Employee, MonthLock, PendingDeviceAttendance


def attendance_row(emp_code, day, status="P", time_in="09:00"):
    return {
        "emp_code": emp_code, "emp_name": f"Name {emp_code}", "department": "DefaultDepartment",
        "designation": "HELPER", "company": "MVICK", "category": "DefaultCategory", "subcategory": "",
        "date": pd.Timestamp(day), "shift": "", "time_in": time_in, "time_out": "17:30",
        "work_hours": 8.5, "ot_hours": 0.0, "status": status,
    }


class UploadNeverCreatesEmployeesTests(TestCase):
    def test_unknown_code_waits_and_no_employee_or_department_is_created(self):
        import_dataframe(pd.DataFrame([attendance_row("N1", "2026-10-05")]))
        self.assertFalse(Employee.objects.filter(code="N1").exists())
        self.assertFalse(Department.objects.filter(name="DefaultDepartment").exists())
        self.assertEqual(PendingDeviceAttendance.objects.filter(emp_code="N1").count(), 1)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_reupload_updates_the_waiting_row(self):
        import_dataframe(pd.DataFrame([attendance_row("N1", "2026-10-05", status="A")]))
        import_dataframe(pd.DataFrame([attendance_row("N1", "2026-10-05", status="P")]))
        self.assertEqual(PendingDeviceAttendance.objects.get(emp_code="N1").status, "P")

    def test_known_code_still_records_attendance(self):
        employee = Employee.objects.create(code="OLD", name="Old")
        import_dataframe(pd.DataFrame([attendance_row("OLD", "2026-10-05")]))
        self.assertTrue(AttendanceRecord.objects.filter(employee=employee).exists())
        self.assertFalse(PendingDeviceAttendance.objects.exists())

    def test_locked_month_is_skipped(self):
        MonthLock.objects.create(year=2026, month=10, view=MonthLock.VIEW_ALL)
        import_dataframe(pd.DataFrame([attendance_row("N1", "2026-10-05")]))
        self.assertFalse(PendingDeviceAttendance.objects.exists())


class AddingPendingEmployeeTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        self.department = Department.objects.create(name="Cutting")

    def post_employee(self, code):
        data = {name: "" for name in EmployeeForm().fields}
        data.update({
            "code": code, "name": "New", "department": self.department.id, "payment_method": "bank_transfer",
            "basic_salary": "0", "hra": "0", "da": "0", "ot_rate_per_hour": "0",
        })
        with mock.patch("attendance.views._require_employee_edit_access", return_value=None):
            response = self.client.post(reverse("employee_create"), data, secure=True)
        self.assertEqual(response.status_code, 302)
        return Employee.objects.get(code=code)

    def starts(self, employee):
        return [p.start_date for p in employee.employment_periods.all()]

    @mock.patch("attendance.views.timezone.localdate", return_value=date(2026, 11, 2))
    def test_new_employee_starts_first_of_month(self, _today):
        self.assertEqual(self.starts(self.post_employee("N3")), [date(2026, 11, 1)])

    @mock.patch("attendance.views.timezone.localdate", return_value=date(2026, 11, 2))
    def test_waiting_attendance_moves_and_start_date_covers_it(self, _today):
        import_dataframe(pd.DataFrame([attendance_row("N3", "2026-10-20"), attendance_row("N3", "2026-10-05")]))
        employee = self.post_employee("N3")
        self.assertEqual(AttendanceRecord.objects.filter(employee=employee).count(), 2)
        self.assertFalse(PendingDeviceAttendance.objects.exists())
        self.assertEqual(self.starts(employee), [date(2026, 10, 1)])

    def test_salary_lists_only_codes_not_in_employees_for_that_month(self):
        old = Employee.objects.create(code="OLD", name="Old")
        AttendanceRecord.objects.create(employee=old, date=date(2026, 10, 1), status="P")
        PendingDeviceAttendance.objects.create(emp_code="OLD", emp_name="Stale", date=date(2026, 10, 2), status="P")
        PendingDeviceAttendance.objects.create(emp_code="N4", emp_name="Waiting", date=date(2026, 10, 2), status="P")
        PendingDeviceAttendance.objects.create(emp_code="N5", emp_name="Other month", date=date(2026, 9, 2), status="P")
        html = self.client.get(reverse("salary"), {"date": "2026-10-01"}, secure=True).content.decode()
        self.assertIn("Waiting", html)
        self.assertIn(f'{reverse("employee_create")}?code=N4', html)
        self.assertNotIn("Stale", html)
        self.assertNotIn("Other month", html)

    def test_add_form_is_prefilled_from_waiting_row(self):
        PendingDeviceAttendance.objects.create(emp_code="N4", emp_name="Waiting", designation="HELPER", date=date(2026, 10, 2))
        with mock.patch("attendance.views._require_employee_edit_access", return_value=None):
            response = self.client.get(reverse("employee_create"), {"code": "N4"}, secure=True)
        self.assertEqual(response.context["form"].initial["name"], "Waiting")


class SalaryDefaultMonthTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        employee = Employee.objects.create(code="OLD", name="Old")
        AttendanceRecord.objects.create(employee=employee, date=date(2026, 11, 2), status="P")

    def opened_month(self):
        return self.client.get(reverse("salary"), secure=True).context["month_name"]

    def test_opens_latest_data_month_when_nothing_is_approved(self):
        self.assertEqual(self.opened_month(), "November")

    @mock.patch("piecerate.models.date", wraps=date)
    def test_opens_first_unapproved_month(self, mocked):
        from piecerate.models import OperatorSummaryApproval

        mocked.today.return_value = date(2026, 11, 5)
        OperatorSummaryApproval.objects.create(year=2026, month=9)
        self.assertEqual(self.opened_month(), "October")


class CompleteMonthButtonTests(TestCase):
    VIEWS = [
        "all", "issues", "ot", "salary_company", "salary_helper", "salary_staff",
        "salary_contractors", "salary_operators", "salary_ironing_bartrack", "salary_fixed_payments",
    ]

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        employee = Employee.objects.create(code="OLD", name="Old")
        AttendanceRecord.objects.create(employee=employee, date=date(2026, 9, 2), status="P")

    def page(self):
        return self.client.get(reverse("salary"), {"date": "2026-09-01"}, secure=True)

    def test_green_once_every_view_is_locked(self):
        for view in self.VIEWS:
            MonthLock.objects.create(year=2026, month=9, view=view)
        self.assertContains(self.page(), "is-done")

    def test_not_green_while_any_view_is_unlocked(self):
        for view in self.VIEWS[:-1]:
            MonthLock.objects.create(year=2026, month=9, view=view)
        self.assertNotContains(self.page(), "is-done")

    def test_completed_month_button_cannot_be_opened(self):
        for view in self.VIEWS:
            MonthLock.objects.create(year=2026, month=9, view=view)
        html = self.page().content.decode()
        self.assertIn('<span class="icon-btn is-done"', html)
        self.assertNotIn('data-tooltip="Complete this month"', html)

    def test_sheets_can_still_be_unlocked_after_completing(self):
        for view in self.VIEWS:
            MonthLock.objects.create(year=2026, month=9, view=view)
        self.assertContains(self.page(), 'data-action="unlock"')

    def lock(self, views):
        return self.client.post(reverse("toggle_month_lock"), {
            "year": 2026, "month": 9, "view": views, "action": "lock", "pin": settings.ATTENDANCE_LOCK_PIN,
            "next": reverse("salary"),
        }, secure=True)

    def test_relocking_only_saves_the_unlocked_sheet(self):
        for view in self.VIEWS:
            if view != "salary_helper":
                MonthLock.objects.create(year=2026, month=9, view=view)
        with mock.patch("attendance.views._snapshot_salary_tab") as snapshot, \
                mock.patch("attendance.views._snapshot_all_employee_details"):
            self.lock(self.VIEWS)
        snapshot.assert_called_once_with(2026, 9, "helpers")
        self.assertTrue(MonthLock.objects.filter(year=2026, month=9, view="salary_helper").exists())
        self.assertEqual(MonthLock.objects.filter(year=2026, month=9).count(), len(self.VIEWS))

    def test_relocking_a_completed_month_changes_nothing(self):
        for view in self.VIEWS:
            MonthLock.objects.create(year=2026, month=9, view=view)
        with mock.patch("attendance.views._snapshot_salary_tab") as snapshot:
            response = self.client.post(reverse("toggle_month_lock"), {
                "year": 2026, "month": 9, "view": self.VIEWS, "action": "lock",
                "pin": settings.ATTENDANCE_LOCK_PIN, "next": reverse("salary"),
            }, secure=True, follow=True)
        snapshot.assert_not_called()
        self.assertContains(response, "Already locked for September 2026")
