from cryptography.fernet import Fernet
print("Fernet key (save as FERNET_KEY):")
print(Fernet.generate_key().decode())
print()
print("Use the printed key as the FERNET_KEY environment variable (Render).")