# Hello World Example

Minimal example showing how a consumer repository uses `nanvix_zutil`.

## Structure

```
hello-world/
├── z              # Bash bootstrap wrapper
├── z.ps1          # PowerShell bootstrap wrapper
├── .nanvix/
│   └── z.py       # ZScript subclass (build logic lives here)
├── src/
│   └── hello.py   # Hello world Python program
└── README.md
```

## Running

```bash
./z setup    # verify Python >= 3.12
./z build    # copy src/hello.py → build/
./z test     # run the built artifact
./z clean    # remove build artifacts
```

