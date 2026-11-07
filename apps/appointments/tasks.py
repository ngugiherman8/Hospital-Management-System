from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Appointment, AppointmentStatus
from .sms import send_sms


@shared_task
def send_due_reminders(hours_ahead: int = 24):
    """SMS every booked patient whose appointment starts within the next `hours_ahead` hours."""
    now = timezone.now()
    due = Appointment.objects.filter(
        status=AppointmentStatus.BOOKED, reminder_sent_at__isnull=True,
        starts_at__gt=now, starts_at__lte=now + timedelta(hours=hours_ahead),
    ).select_related("patient", "doctor", "clinic")
    sent = 0
    for appt in due:
        when = timezone.localtime(appt.starts_at)
        msg = (f"{settings.HOSPITAL_NAME}: reminder for {appt.patient.first_name}. "
               f"{appt.clinic.name} on {when:%a %d %b} at {when:%H:%M}. Reply or call to reschedule.")
        if send_sms(appt.patient.phone_normalized, msg):
            Appointment.objects.filter(pk=appt.pk).update(reminder_sent_at=now)
            sent += 1
    return sent


@shared_task
def mark_no_shows():
    """Appointments still 'booked' after the day ends become no-shows."""
    cutoff = timezone.now() - timedelta(hours=12)
    return Appointment.objects.filter(status=AppointmentStatus.BOOKED, starts_at__lt=cutoff).update(
        status=AppointmentStatus.NO_SHOW
    )
