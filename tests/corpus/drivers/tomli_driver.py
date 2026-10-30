import tomli

data = tomli.loads('title = "x"\n[owner]\nname = "y"\ndob = 1979-05-27T07:32:00Z\nnums = [1, 2.5]\n')
print(data)
try:
    tomli.loads("= broken")
except tomli.TOMLDecodeError as e:
    print("error", type(e).__name__)
