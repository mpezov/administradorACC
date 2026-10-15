import jwt  # pip install pyjwt
import json

with open('acc_tokens.json') as f:
    data = json.load(f)
token = data['access_token']
decoded = jwt.decode(token, options={"verify_signature": False})
print(decoded.get('scope', 'No scope'))