"""Other apps listen here instead of being called by HR, so HR does not depend on them."""

from django.dispatch import Signal

# Sent when a leave request is approved. Receivers return how many things they changed (for example shifts released).
leave_approved = Signal()
