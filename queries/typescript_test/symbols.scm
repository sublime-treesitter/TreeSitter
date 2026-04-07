; inherits: typescript

; test vs it: These are functionally identical and used to define individual test cases
; describe vs suite: These are used to group related tests into logical blocks
; test/it do not need to be nested under describe/suite blocks, they can live at the top level of a file

(call_expression
  function: (identifier) @name
  (#match? @name "^(describe|suite|it|test)$")
  arguments: (arguments
    (string) @definition.test @breadcrumb.1))
