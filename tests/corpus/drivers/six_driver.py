import six
from six.moves import range as srange

print(six.PY3, list(srange(3)), six.text_type("x"), six.ensure_str(b"abc"))
