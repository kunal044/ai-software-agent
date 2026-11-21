import { readFileSync } from 'node:fs'
import { ESLint } from '../../frontend/node_modules/eslint/lib/api.js'
import ts from '../../frontend/node_modules/typescript/lib/typescript.js'

const request = JSON.parse(readFileSync(0, 'utf8'))
const { code, extension, lint } = request
const name = 'source.' + extension
const source = ts.createSourceFile(name, code, ts.ScriptTarget.Latest, true)
const diagnostics = ts.transpileModule(code, {
  fileName: name, reportDiagnostics: true,
  compilerOptions: { target: ts.ScriptTarget.ESNext, jsx: ts.JsxEmit.Preserve },
}).diagnostics
const issues = diagnostics.filter(issue => issue.file && issue.category === ts.DiagnosticCategory.Error).map(issue => ({
  line: issue.file.getLineAndCharacterOfPosition(issue.start || 0).line + 1,
  rule: 'TS' + issue.code, message: ts.flattenDiagnosticMessageText(issue.messageText, ' '), category: 'syntax',
}))
if (lint && !issues.length && ['js', 'jsx', 'mjs', 'cjs'].includes(extension)) {
  const eslint = new ESLint({
    overrideConfigFile: true,
    allowInlineConfig: false,
    overrideConfig: [{
      files: ['**/*.{js,jsx,mjs,cjs}'],
      languageOptions: { ecmaVersion: 'latest', sourceType: extension === 'cjs' ? 'commonjs' : 'module',
        parserOptions: { ecmaFeatures: { jsx: true } } },
      rules: { 'no-unused-vars': 'warn', 'no-constant-condition': 'warn', 'no-unreachable': 'warn', 'no-dupe-keys': 'error' },
    }],
  })
  const [result] = await eslint.lintText(code, { filePath: name })
  for (const issue of result.messages) issues.push({
    line: issue.line || 1, rule: issue.ruleId || 'ESLINT-PARSE', message: issue.message,
    category: issue.fatal ? 'syntax' : 'static',
  })
}

if (lint && !issues.length && ['ts', 'tsx', 'mts', 'cts'].includes(extension)) {
  const options = { noLib: true, noResolve: true, noEmit: true, target: ts.ScriptTarget.ESNext, jsx: ts.JsxEmit.Preserve, types: [] }
  const host = {
    getSourceFile: path => path === name ? source : undefined,
    getDefaultLibFileName: () => '',
    getCurrentDirectory: () => '',
    getDirectories: () => [],
    useCaseSensitiveFileNames: () => true,
    getCanonicalFileName: path => path,
    getNewLine: () => '\n',
    fileExists: path => path === name,
    readFile: path => path === name ? code : undefined,
    writeFile: () => {},
  }
  const program = ts.createProgram([name], options, host)
  const localErrors = new Set([2322, 2345, 2352, 2365, 2367, 2554])
  for (const issue of program.getSemanticDiagnostics(source)) {
    if (!localErrors.has(issue.code)) continue
    issues.push({ line: source.getLineAndCharacterOfPosition(issue.start || 0).line + 1,
      rule: 'TS' + issue.code, message: ts.flattenDiagnosticMessageText(issue.messageText, ' '), category: 'static' })
  }
}

const symbols = source.statements.filter(node => ts.isFunctionDeclaration(node) && node.name).map(node => ({
  name: node.name.text,
  start: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
  end: source.getLineAndCharacterOfPosition(node.getEnd()).line + 1,
}))
process.stdout.write(JSON.stringify({ issues, symbols }))

