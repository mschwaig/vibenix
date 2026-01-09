# Vibenix - AI assistant for nix packaging

The elevator pitch for this project is that you give it the URL of a GitHub project,
and it starts from a template and iteratively improves on that until it finally succeeds (or fails) to build the package.

### My plans with this work

I want to publish a paper based on this work, so if you're interested in that or want to collaborate on related work send me a message.

I could also imagine providing this functionality as a hosted service to customers, so if you are curious about that, please send me an email as well.

### How to run this

To run vibenix with the default terminal UI:
```
nix develop -c python -m vibenix
```

You will be asked to pick a model and also provide an API key if required.

**Note:** There is also a `--textual` flag for a Textual-based UI, but it is currently unmaintained and not recommended for use.
