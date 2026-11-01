class RegistrationEmailError(Exception):
    """Ошибка регистрации: введите действительный адрес электронной почты."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)
