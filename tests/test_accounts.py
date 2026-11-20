from apps.accounts.models import Role
from apps.core.permissions import role_required


def test_role_checks(reception, nurse, admin_user):
    assert reception.has_role(Role.RECEPTION)
    assert not reception.has_role(Role.NURSE, Role.DOCTOR)
    assert admin_user.has_role(Role.PHARMACIST)  # admins can do everything
    assert nurse.has_role(Role.NURSE)


def test_anonymous_is_sent_to_login(client):
    response = client.get("/patients/")
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_role_required_decorator_blocks_other_roles(rf, nurse):
    @role_required(Role.RECEPTION)
    def view(request):
        raise AssertionError("should not run")

    request = rf.get("/")
    request.user = nurse
    import pytest
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        view(request)
