"""Key-shaped test values, built in pieces: a literal that looks like a real
key is rejected by the push checks. None of them opens anything."""
OPENAI = "sk-proj-" + "Qx7Lm2Vn9Rt4Wp8Zk3Jh6Fd1Sb5Yc0Ga" * 2
ANTHROPIC = "sk-ant-" + "api03-" + "Zk3Jh6Fd1Sb5Yc0GaQx7Lm2Vn9Rt4Wp8" * 2
GITHUB = "ghp_" + "R4m8Tz2Kc9" * 3 + "W1x5Yb"
STRIPE = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
HEX = "9f2c4e7a1b3d5f60" * 4
PASSWORD = "Tr0ub4dor&3xq"
SHELLY = "pa$$w0rd;rm-x9Kq2Lm8"
JWT = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0", "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"])
PEM = "-----BEGIN PRIVATE KEY-----\n" + "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n" * 3 + "-----END PRIVATE KEY-----"
