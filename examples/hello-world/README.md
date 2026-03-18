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
│   └── main.c     # Hello world C program
├── Makefile       # Build rules
└── README.md
```

## Running

```bash
./z setup    # verify gcc is available
./z build    # compile src/main.c → build/hello
./z test     # run the binary
./z clean    # remove build artifacts
```
