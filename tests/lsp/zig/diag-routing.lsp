> {"id":1,"method":"initialize","params":{"processId":null,"rootUri":null,"capabilities":{"general":{"positionEncodings":["utf-16"]}}}}
< {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"positionEncoding":"utf-16","textDocumentSync":{"openClose":true,"change":1},"documentFormattingProvider":true,"hoverProvider":true,"completionProvider":{},"definitionProvider":true,"documentSymbolProvider":true,"referencesProvider":true,"renameProvider":true,"signatureHelpProvider":{"triggerCharacters":["(",","]},"semanticTokensProvider":{"legend":{"tokenTypes":["function","struct","enum","enumMember","variable","parameter","property"],"tokenModifiers":[]},"full":true}},"serverInfo":{"name":"taixu","title":"太虚工具链 LSP"}}}
> {"notify":"initialized"}
> {"open":"lsp-importer.tx","version":1}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-importer.tx","diagnostics":[{"range":{"start":{"line":0,"character":0},"end":{"line":0,"character":0}},"severity":1,"code":3008,"source":"taixu","message":"无法读取模块 <dir>/lsp-nope.tx：FileNotFound"},{"range":{"start":{"line":2,"character":0},"end":{"line":2,"character":0}},"severity":1,"code":3008,"source":"taixu","message":"找不到导入目标模块 './lsp-nope'"},{"range":{"start":{"line":2,"character":4},"end":{"line":2,"character":4}},"severity":1,"code":3002,"source":"taixu","message":"绑定 's' 声明为 string，初值为 int"},{"range":{"start":{"line":5,"character":21},"end":{"line":5,"character":21}},"severity":1,"code":3001,"source":"taixu","message":"未定义函数 'gone'"}]}}
> {"open":"lsp-err.tx","version":1}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-err.tx","diagnostics":[{"range":{"start":{"line":2,"character":4},"end":{"line":2,"character":4}},"severity":1,"code":3002,"source":"taixu","message":"绑定 's' 声明为 string，初值为 int"}]}}
> {"change":"lsp-err.tx","version":2,"replace":["const s: string = n;","const s: int = n;"]}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-err.tx","diagnostics":[]}}
> {"id":2,"method":"textDocument/hover","params":{"textDocument":{"uri":"<uri>/lsp-importer.tx"},"position":{"line":5,"character":12}}}
< {"jsonrpc":"2.0","id":2,"result":null}
> {"id":3,"method":"textDocument/definition","params":{"textDocument":{"uri":"<uri>/lsp-importer.tx"},"position":{"line":5,"character":12}}}
< {"jsonrpc":"2.0","id":3,"result":{"uri":"<uri>/lsp-err.tx","range":{"start":{"line":1,"character":16},"end":{"line":1,"character":20}}}}
> {"close":"lsp-err.tx"}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-err.tx","diagnostics":[]}}
> {"id":4,"method":"shutdown"}
< {"jsonrpc":"2.0","id":4,"result":null}
> {"notify":"exit"}
= {"exit":0}
