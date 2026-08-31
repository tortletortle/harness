from service import create_user


def test_create_user():
    u = create_user('alice', 'a@x.com', 'u1')
    assert u.user_id == 'u1'