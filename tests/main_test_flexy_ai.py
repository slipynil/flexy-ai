
from src.flexy_ai.registration import register_user
from src.generate_username import generate_random_username


def main():

    mail = "rosose7340@davopa.com"

    # 2. регистрируем пользователя в flexy.ai
    register_user(
        login=generate_random_username(),
        email=mail,
        password="kraken1488",
    )

    print("User registered successfully")

if __name__ == "__main__":
    main()
