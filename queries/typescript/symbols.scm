; inherits: ecma

(class_declaration (type_identifier) @definition.class @breadcrumb.1)

(type_alias_declaration (type_identifier) @definition.type)
(interface_declaration (type_identifier) @definition.interface)

(program (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object @breadcrumb.1 (as_expression (object)))
    ((identifier) @definition.var (as_expression [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array) (member_expression)
    ]))
  ]
)))
(program (export_statement (lexical_declaration (variable_declarator
  [
    ((identifier) @definition.object @breadcrumb.1 (as_expression (object)))
    ((identifier) @definition.var (as_expression [
      (number) (string) (template_string) (null) (undefined) (new_expression) (call_expression) (array) (member_expression)
    ]))
  ]
))))
