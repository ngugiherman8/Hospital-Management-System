from django.contrib.auth.mixins import LoginRequiredMixin


class RoleRequiredMixin(LoginRequiredMixin):
    """Restrict a view to the listed roles. Superusers always pass."""

    allowed_roles = ()
    raise_exception = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_role(*self.allowed_roles):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


ADMIN = ("admin",)
FRONT_DESK = ("admin", "receptionist")
CLINICAL = ("admin", "doctor", "nurse")
FRONT_DESK_AND_CLINICAL = ("admin", "receptionist", "doctor", "nurse")
