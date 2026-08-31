from models import User

def create_user(name, email, user_id):
    return User(user_id, name, email)