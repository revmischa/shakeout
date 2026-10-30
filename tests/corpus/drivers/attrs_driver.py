import attrs


@attrs.define
class Point:
    x: int
    y: int = attrs.field(default=0, validator=attrs.validators.instance_of(int))


@attrs.frozen
class Box:
    items: list = attrs.Factory(list)


p = Point(1, 2)
print(p, attrs.asdict(p), attrs.evolve(p, y=5))
print(Box(), attrs.fields(Point).x.name)
try:
    Point(1, "no")
except TypeError as e:
    print("TypeError", type(e).__name__)
