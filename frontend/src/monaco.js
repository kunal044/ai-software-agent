import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/editor.js'
import 'monaco-editor/internal/common/workers.js'
import 'monaco-editor/languages/definitions/cpp/register.js'
import 'monaco-editor/languages/definitions/css/register.js'
import 'monaco-editor/languages/definitions/html/register.js'
import 'monaco-editor/languages/definitions/ini/register.js'
import 'monaco-editor/languages/definitions/java/register.js'
import 'monaco-editor/languages/definitions/javascript/register.js'
import 'monaco-editor/languages/definitions/markdown/register.js'
import 'monaco-editor/languages/definitions/python/register.js'
import 'monaco-editor/languages/definitions/typescript/register.js'
import 'monaco-editor/languages/definitions/yaml/register.js'
import 'monaco-editor/languages/features/json/register.js'
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'
import JsonWorker from 'monaco-editor/languages/features/json/json.worker.js?worker'

globalThis.MonacoEnvironment = {
  getWorker(_module, label) {
    return label === 'json' ? new JsonWorker() : new EditorWorker()
  },
}
loader.config({ monaco })

export { monaco }
export { default } from '@monaco-editor/react'

