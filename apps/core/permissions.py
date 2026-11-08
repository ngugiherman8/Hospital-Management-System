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


def role_required(*roles):
    """Function-view decorator: sign-in required, then one of the roles (admins always pass)."""
    from functools import wraps

    from django.contrib.auth.views import redirect_to_login

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.has_role("admin", *roles):
                from django.core.exceptions import PermissionDenied

                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


ADMIN = ("admin",)
FRONT_DESK = ("admin", "receptionist")
CLINICAL = ("admin", "doctor", "nurse")
FRONT_DESK_AND_CLINICAL = ("admin", "receptionist", "doctor", "nurse")
