; inherits: typescript

(call_expression
  function: (identifier) @name
  (#match? @name "^(describe|suite|it|test)$")
  arguments: (arguments
    (string) @definition.test @breadcrumb.1))
