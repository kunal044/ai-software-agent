import com.sun.source.util.JavacTask;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import javax.tools.*;

class JavaSyntax {
    public static void main(String[] args) throws Exception {
        String text = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
        var compiler = ToolProvider.getSystemJavaCompiler();
        var messages = new DiagnosticCollector<JavaFileObject>();
        var source = new SimpleJavaFileObject(URI.create("string:///Source.java"), JavaFileObject.Kind.SOURCE) {
            @Override public CharSequence getCharContent(boolean ignoreEncodingErrors) { return text; }
        };
        try (var files = compiler.getStandardFileManager(messages, Locale.ROOT, StandardCharsets.UTF_8)) {
            var task = (JavacTask) compiler.getTask(null, files, messages,
                List.of("-proc:none"), null, List.of(source));
            task.parse();
        }
        for (var item : messages.getDiagnostics()) {
            if (item.getKind() != Diagnostic.Kind.ERROR) continue;
            var message = item.getMessage(Locale.ROOT).replaceAll("[\\r\\n\\t]+", " ");
            System.out.println(item.getLineNumber() + "\t" + item.getCode() + "\t" + message);
        }
    }
}

