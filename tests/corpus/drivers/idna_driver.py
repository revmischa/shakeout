import idna

print(idna.encode("ドメイン.テスト"))
print(idna.decode(b"xn--eckwd4c7c.xn--zckzah"))
try:
    idna.encode("a..b")
except idna.IDNAError as e:
    print("error", type(e).__name__)
