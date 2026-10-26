> {"id":1,"method":"initialize","params":{"processId":null,"rootUri":null,"capabilities":{}}}
< {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"positionEncoding":"utf-8","textDocumentSync":{"openClose":true,"change":1},"documentFormattingProvider":true,"hoverProvider":true,"completionProvider":{},"definitionProvider":true,"documentSymbolProvider":true,"referencesProvider":true,"renameProvider":true,"signatureHelpProvider":{"triggerCharacters":["(",","]},"semanticTokensProvider":{"legend":{"tokenTypes":["function","struct","enum","enumMember","variable","parameter","property"],"tokenModifiers":[]},"full":true}},"serverInfo":{"name":"taixu","title":"太虚工具链 LSP"}}}
> {"notify":"initialized"}
> {"open":"lsp-zh.tx","version":1}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-zh.tx","diagnostics":[]}}
> {"id":2,"method":"textDocument/hover","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":31}}}
< {"jsonrpc":"2.0","id":2,"result":{"contents":{"kind":"markdown","value":"function dist(p: Ref<Point>): int"},"range":{"start":{"line":12,"character":30},"end":{"line":12,"character":34}}}}
> {"id":3,"method":"textDocument/definition","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":31}}}
< {"jsonrpc":"2.0","id":3,"result":{"uri":"<uri>/lsp-zh.tx","range":{"start":{"line":6,"character":16},"end":{"line":6,"character":20}}}}
> {"id":4,"method":"shutdown"}
< {"jsonrpc":"2.0","id":4,"result":null}
> {"notify":"exit"}
= {"exit":0}
