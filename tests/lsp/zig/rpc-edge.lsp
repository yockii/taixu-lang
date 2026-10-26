> {"id":1,"method":"textDocument/hover","params":{"textDocument":{"uri":"file:///nowhere.tx"},"position":{"line":0,"character":0}}}
< {"jsonrpc":"2.0","id":1,"result":null}
> {"id":2,"method":"initialize","params":{"processId":null,"rootUri":null,"capabilities":{}}}
< {"jsonrpc":"2.0","id":2,"result":{"capabilities":{"positionEncoding":"utf-8","textDocumentSync":{"openClose":true,"change":1},"documentFormattingProvider":true,"hoverProvider":true,"completionProvider":{},"definitionProvider":true,"documentSymbolProvider":true,"referencesProvider":true,"renameProvider":true,"signatureHelpProvider":{"triggerCharacters":["(",","]},"semanticTokensProvider":{"legend":{"tokenTypes":["function","struct","enum","enumMember","variable","parameter","property"],"tokenModifiers":[]},"full":true}},"serverInfo":{"name":"taixu","title":"太虚工具链 LSP"}}}
> {"notify":"initialized"}
> {"raw":"not json"}
< {"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"}}
> {"raw":"[1,2]"}
< {"jsonrpc":"2.0","error":{"code":-32600,"message":"Invalid Request"}}
> {"raw":"{\"jsonrpc\":\"2.0\",\"id\":77}"}
> {"id":3,"method":"$/frobnicate","params":{}}
= {"harness":"no response"}
> {"id":4,"method":"workspace/symbol","params":{"query":"x"}}
< {"jsonrpc":"2.0","id":4,"error":{"code":-32601,"message":"Method not found"}}
> {"notify":"$/cancelRequest","params":{"id":1}}
> {"notify":"workspace/didChangeConfiguration","params":{"settings":{}}}
> {"open":"lsp-geom.tx","version":1}
< {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"<uri>/lsp-geom.tx","diagnostics":[]}}
> {"change":"lsp-geom.tx","version":2,"ranged":{"range":{"start":{"line":0,"character":0},"end":{"line":0,"character":0}},"text":"// x\n"}}
= {"harness":"no diagnostics"}
> {"id":5,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"<uri>/lsp-geom.tx"}}}
< {"jsonrpc":"2.0","id":5,"result":[{"name":"Rect","kind":23,"range":{"start":{"line":2,"character":14},"end":{"line":2,"character":18}},"selectionRange":{"start":{"line":2,"character":14},"end":{"line":2,"character":18}}},{"name":"area","kind":12,"range":{"start":{"line":7,"character":16},"end":{"line":7,"character":20}},"selectionRange":{"start":{"line":7,"character":16},"end":{"line":7,"character":20}}},{"name":"UNIT","kind":14,"range":{"start":{"line":11,"character":7},"end":{"line":11,"character":11}},"selectionRange":{"start":{"line":11,"character":7},"end":{"line":11,"character":11}}}]}
> {"id":"s1","method":"textDocument/hover","params":{"textDocument":{"uri":"<uri>/lsp-geom.tx"},"position":{"line":8,"character":17}}}
< {"jsonrpc":"2.0","id":"s1","result":null}
> {"id":6,"method":"shutdown"}
< {"jsonrpc":"2.0","id":6,"result":null}
> {"id":7,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"<uri>/lsp-geom.tx"}}}
< {"jsonrpc":"2.0","id":7,"result":[{"name":"Rect","kind":23,"range":{"start":{"line":2,"character":14},"end":{"line":2,"character":18}},"selectionRange":{"start":{"line":2,"character":14},"end":{"line":2,"character":18}}},{"name":"area","kind":12,"range":{"start":{"line":7,"character":16},"end":{"line":7,"character":20}},"selectionRange":{"start":{"line":7,"character":16},"end":{"line":7,"character":20}}},{"name":"UNIT","kind":14,"range":{"start":{"line":11,"character":7},"end":{"line":11,"character":11}},"selectionRange":{"start":{"line":11,"character":7},"end":{"line":11,"character":11}}}]}
> {"notify":"exit"}
= {"exit":0}
