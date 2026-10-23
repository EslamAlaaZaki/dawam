# Vendored BPE files

`cl100k_base` and `o200k_base` from OpenAI's tiktoken (https://github.com/openai/tiktoken,
MIT licence, Copyright (c) 2022 OpenAI, Shantanu Jain). They are stored under the file name
tiktoken's cache expects (the SHA-1 of the download URL) so they load from disk with no
network; tiktoken checks each file against its published SHA-256.

- `9b5ad71b2ce5302211f9c61530b329a4922fc6a4`: `cl100k_base`
- `fb374d419588a4632f3f557e76b4b70aebbca790`: `o200k_base`
