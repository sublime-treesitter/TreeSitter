(function_declaration (identifier) @definition.function)
(variable_declarator (identifier) @definition.function (arrow_function))
(method_definition (property_identifier) @definition.function)
(pair (property_identifier) @definition.function (arrow_function))
(class_declaration (type_identifier) @definition.class.depth.1)

(type_alias_declaration (type_identifier) @definition.type)
(interface_declaration (type_identifier) @definition.interface)

(program (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object.depth.1 (object))
    ((identifier) @definition.var [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array)
    ])
  ]
)))
(program (export_statement (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object.depth.1 (object))
    ((identifier) @definition.var [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array)
    ])
  ]
))))

(program (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object.depth.1 (as_expression (object)))
    ((identifier) @definition.var (as_expression [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array)
    ]))
  ]
)))
(program (export_statement (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object.depth.1 (as_expression (object)))
    ((identifier) @definition.var (as_expression [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array)
    ]))
  ]
))))
