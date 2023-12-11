; Note: there is no "ecma" language, this is for extending only

(function_declaration (identifier) @definition.function)
(variable_declarator (identifier) @definition.function (arrow_function))
(method_definition (property_identifier) @definition.function)
(pair (property_identifier) @definition.function (arrow_function))

(program (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object.depth.1 (object))
    ((identifier) @definition.var [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array) (member_expression)
    ])
  ]
)))
(program (export_statement (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object.depth.1 (object))
    ((identifier) @definition.var [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array) (member_expression)
    ])
  ]
))))
