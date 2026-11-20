from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Function-view decorator: only users with one of these roles (or admins)."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if not user.has_role(*roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class RoleRequiredMixin(LoginRequiredMixin):
    """Class-view mixin: set `allowed_roles` on the view."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_role(*self.allowed_roles):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
