(module (expression_statement (assignment left: (identifier) @definition.var)))
(module (expression_statement (assignment left: (pattern_list (identifier) @definition.var))))

(aliased_import (identifier) @definition.var)

(class_definition name: (identifier) @definition.class @breadcrumb.1)

(function_definition name: (identifier) @definition.function @breadcrumb.1)
