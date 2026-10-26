> {"id":1,"method":"initialize","params":{"processId":null,"rootUri":null,"capabilities":{"general":{"positionEncodings":["utf-16"]}}}}
< {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"positionEncoding":"utf-16","textDocumentSync":{"openClose":true,"change":1},"documentFormattingProvider":true,"hoverProvider":true,"completionProvider":{},"definitionProvider":true,"documentSymbolProvider":true,"referencesProvider":true,"renameProvider":true,"signatureHelpProvider":{"triggerCharacters":["(",","]},"semanticTokensProvider":{"legend":{"tokenTypes":["function","struct","enum","enumMember","variable","parameter","property"],"tokenModifiers":[]},"full":true}},"serverInfo":{"name":"taixu","title":"太虚工具链 LSP"}}}
> {"notify":"initialized"}
> {"open":"lsp-zh.tx","version":1}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-zh.tx","diagnostics":[]}}
> {"id":2,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"}}}
< {"jsonrpc":"2.0","id":2,"result":[{"name":"Point","kind":23,"range":{"start":{"line":1,"character":14},"end":{"line":1,"character":19}},"selectionRange":{"start":{"line":1,"character":14},"end":{"line":1,"character":19}}},{"name":"dist","kind":12,"range":{"start":{"line":6,"character":16},"end":{"line":6,"character":20}},"selectionRange":{"start":{"line":6,"character":16},"end":{"line":6,"character":20}}},{"name":"main","kind":12,"range":{"start":{"line":10,"character":16},"end":{"line":10,"character":20}},"selectionRange":{"start":{"line":10,"character":16},"end":{"line":10,"character":20}}}]}
> {"id":3,"method":"textDocument/hover","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":25}}}
< {"jsonrpc":"2.0","id":3,"result":{"contents":{"kind":"markdown","value":"function dist(p: Ref<Point>): int"},"range":{"start":{"line":12,"character":24},"end":{"line":12,"character":28}}}}
> {"id":4,"method":"textDocument/definition","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":25}}}
< {"jsonrpc":"2.0","id":4,"result":{"uri":"<uri>/lsp-zh.tx","range":{"start":{"line":6,"character":16},"end":{"line":6,"character":20}}}}
> {"id":5,"method":"textDocument/references","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":25},"context":{"includeDeclaration":true}}}
< {"jsonrpc":"2.0","id":5,"result":[{"uri":"<uri>/lsp-zh.tx","range":{"start":{"line":6,"character":16},"end":{"line":6,"character":20}}},{"uri":"<uri>/lsp-zh.tx","range":{"start":{"line":12,"character":24},"end":{"line":12,"character":28}}},{"uri":"<uri>/lsp-zh.tx","range":{"start":{"line":13,"character":21},"end":{"line":13,"character":25}}}]}
> {"id":6,"method":"textDocument/rename","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":25},"newName":"measure"}}
< {"jsonrpc":"2.0","id":6,"result":{"changes":{"<uri>/lsp-zh.tx":[{"range":{"start":{"line":6,"character":16},"end":{"line":6,"character":20}},"newText":"measure"},{"range":{"start":{"line":12,"character":24},"end":{"line":12,"character":28}},"newText":"measure"},{"range":{"start":{"line":13,"character":21},"end":{"line":13,"character":25}},"newText":"measure"}]}}}
> {"id":7,"method":"textDocument/rename","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":14,"character":4},"newName":"sum"}}
< {"jsonrpc":"2.0","id":7,"result":{"changes":{"<uri>/lsp-zh.tx":[{"range":{"start":{"line":13,"character":8},"end":{"line":13,"character":13}},"newText":"sum"},{"range":{"start":{"line":14,"character":4},"end":{"line":14,"character":9}},"newText":"sum"},{"range":{"start":{"line":14,"character":12},"end":{"line":14,"character":17}},"newText":"sum"},{"range":{"start":{"line":15,"character":11},"end":{"line":15,"character":16}},"newText":"sum"}]}}}
> {"id":8,"method":"textDocument/signatureHelp","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"position":{"line":12,"character":29}}}
< {"jsonrpc":"2.0","id":8,"result":{"signatures":[{"label":"function dist(p: Ref<Point>): int","parameters":[{"label":[14,27]}]}],"activeSignature":0,"activeParameter":0}}
> {"id":9,"method":"textDocument/semanticTokens/full","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"}}}
< {"jsonrpc":"2.0","id":9,"result":{"data":[1,14,5,1,0,1,4,1,6,0,1,4,1,6,0,3,16,4,0,0,0,5,1,5,0,0,8,5,1,0,1,11,1,5,0,0,2,1,6,0,0,4,1,5,0,0,2,1,6,0,3,16,4,0,0,0,5,4,5,0,1,10,1,4,0,0,8,5,1,0,0,13,5,1,0,0,8,1,6,0,0,6,1,6,0,1,4,5,0,0,0,14,5,0,0,0,6,4,0,0,0,5,1,4,0,1,8,5,4,0,0,13,4,0,0,0,5,1,4,0,1,4,5,4,0,0,8,5,4,0,1,11,5,4,0]}}
> {"id":10,"method":"textDocument/formatting","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"options":{"tabSize":4,"insertSpaces":true}}}
< {"jsonrpc":"2.0","id":10,"result":[]}
> {"change":"lsp-zh.tx","version":2,"text":"export function f(n: int): int {\n  return n;\n}"}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-zh.tx","diagnostics":[]}}
> {"id":11,"method":"textDocument/formatting","params":{"textDocument":{"uri":"<uri>/lsp-zh.tx"},"options":{"tabSize":4,"insertSpaces":true}}}
< {"jsonrpc":"2.0","id":11,"result":[{"range":{"start":{"line":0,"character":0},"end":{"line":2,"character":0}},"newText":"export function f(n: int): int {\n    return n;\n}\n"}]}
> {"id":12,"method":"shutdown"}
< {"jsonrpc":"2.0","id":12,"result":null}
> {"notify":"exit"}
= {"exit":0}
