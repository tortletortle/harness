from models import User

def create_user(name, email, user_id):
    return User(name, email, user_id)
