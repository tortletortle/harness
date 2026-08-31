from service import create_user

def test_create_user():
    u = create_user('u1', 'alice', 'a@x.com')
    assert u.user_id == 'u1'
    assert u.name == 'alice'
    assert u.email == 'a@x.com'