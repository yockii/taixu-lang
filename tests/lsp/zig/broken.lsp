> {"id":1,"method":"initialize","params":{"processId":null,"rootUri":null,"capabilities":{"general":{"positionEncodings":["utf-16"]}}}}
< {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"positionEncoding":"utf-16","textDocumentSync":{"openClose":true,"change":1},"documentFormattingProvider":true,"hoverProvider":true,"completionProvider":{},"definitionProvider":true,"documentSymbolProvider":true,"referencesProvider":true,"renameProvider":true,"signatureHelpProvider":{"triggerCharacters":["(",","]},"semanticTokensProvider":{"legend":{"tokenTypes":["function","struct","enum","enumMember","variable","parameter","property"],"tokenModifiers":[]},"full":true}},"serverInfo":{"name":"taixu","title":"太虚工具链 LSP"}}}
> {"notify":"initialized"}
> {"open":"lsp-broken.tx","version":1}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-broken.tx","diagnostics":[{"range":{"start":{"line":6,"character":14},"end":{"line":6,"character":14}},"severity":1,"code":2001,"source":"taixu","message":"期望表达式，遇到 ';'"},{"range":{"start":{"line":5,"character":16},"end":{"line":5,"character":16}},"severity":1,"code":3012,"source":"taixu","message":"函数 bad 声明返回 int，但存在未 return 的路径"}]}}
> {"id":2,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"<uri>/lsp-broken.tx"}}}
< {"jsonrpc":"2.0","id":2,"result":[{"name":"ok","kind":12,"range":{"start":{"line":1,"character":16},"end":{"line":1,"character":18}},"selectionRange":{"start":{"line":1,"character":16},"end":{"line":1,"character":18}}},{"name":"bad","kind":12,"range":{"start":{"line":5,"character":16},"end":{"line":5,"character":19}},"selectionRange":{"start":{"line":5,"character":16},"end":{"line":5,"character":19}}},{"name":"main","kind":12,"range":{"start":{"line":9,"character":16},"end":{"line":9,"character":20}},"selectionRange":{"start":{"line":9,"character":16},"end":{"line":9,"character":20}}}]}
> {"id":3,"method":"textDocument/hover","params":{"textDocument":{"uri":"<uri>/lsp-broken.tx"},"position":{"line":10,"character":11}}}
< {"jsonrpc":"2.0","id":3,"result":{"contents":{"kind":"markdown","value":"function ok(n: int): int"},"range":{"start":{"line":10,"character":11},"end":{"line":10,"character":13}}}}
> {"id":4,"method":"textDocument/semanticTokens/full","params":{"textDocument":{"uri":"<uri>/lsp-broken.tx"}}}
< {"jsonrpc":"2.0","id":4,"result":{"data":[1,16,2,0,0,0,3,1,5,0,1,11,1,5,0,3,16,3,0,0,0,4,1,5,0,4,16,4,0,0,0,5,4,5,0,1,11,2,0,0]}}
> {"id":5,"method":"textDocument/formatting","params":{"textDocument":{"uri":"<uri>/lsp-broken.tx"},"options":{"tabSize":4,"insertSpaces":true}}}
< {"jsonrpc":"2.0","id":5,"result":[]}
> {"change":"lsp-broken.tx","version":2,"replace":["return n +;","return n + 2;"]}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-broken.tx","diagnostics":[]}}
> {"id":6,"method":"textDocument/references","params":{"textDocument":{"uri":"<uri>/lsp-broken.tx"},"position":{"line":10,"character":11},"context":{"includeDeclaration":true}}}
< {"jsonrpc":"2.0","id":6,"result":[{"uri":"<uri>/lsp-broken.tx","range":{"start":{"line":1,"character":16},"end":{"line":1,"character":18}}},{"uri":"<uri>/lsp-broken.tx","range":{"start":{"line":10,"character":11},"end":{"line":10,"character":13}}}]}
> {"id":7,"method":"shutdown"}
< {"jsonrpc":"2.0","id":7,"result":null}
> {"notify":"exit"}
= {"exit":0}
