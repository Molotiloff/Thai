'use client';

import {useEffect,useId,useRef,useState} from 'react';
import styles from './formControls.module.css';

export function SearchableSelect({name,label,options,defaultValue='',required,onSelectionChange}:{
  name:string;
  label:string;
  options:readonly (readonly [string,string])[];
  defaultValue?:string;
  required:boolean;
  onSelectionChange:(form:HTMLFormElement)=>void;
}) {
  const listId=useId();
  const input=useRef<HTMLInputElement>(null);
  const hidden=useRef<HTMLInputElement>(null);
  const [selectedId,setSelectedId]=useState(defaultValue);
  const [text,setText]=useState(()=>options.find(([id])=>id===defaultValue)?.[1]??'');
  const [open,setOpen]=useState(false);
  const [highlight,setHighlight]=useState(-1);
  const query=text.trim().toLocaleLowerCase();
  const expanded=open&&!!query;
  const matches=expanded?options.filter(([,label])=>label.toLocaleLowerCase().includes(query)).slice(0,20):[];

  useEffect(()=>{
    if(highlight>=0)document.getElementById(`${listId}-${highlight}`)?.scrollIntoView({block:'nearest'});
  },[highlight,listId]);

  function select(id:string,label:string) {
    setSelectedId(id);
    setText(label);
    setOpen(false);
    setHighlight(-1);
    input.current?.setCustomValidity('');
    if(hidden.current?.form){
      hidden.current.value=id;
      onSelectionChange(hidden.current.form);
    }
  }

  return <span className={styles.search}>
    <input type="hidden" name={name} ref={hidden} value={selectedId}/>
    <input ref={input} type="text" role="combobox" aria-label={label} aria-autocomplete="list" aria-expanded={expanded}
      aria-controls={expanded?listId:undefined}
      aria-activedescendant={matches[highlight]?`${listId}-${highlight}`:undefined}
      value={text} required={required} autoComplete="off" placeholder="Введите имя"
      onBlur={()=>{setOpen(false);setHighlight(-1);}}
      onChange={event=>{
        const value=event.currentTarget.value;
        if(hidden.current)hidden.current.value='';
        setSelectedId('');
        event.currentTarget.setCustomValidity(value?'Выберите клиента из списка':'');
        setText(value);
        setHighlight(-1);
        setOpen(true);
        if(event.currentTarget.form)onSelectionChange(event.currentTarget.form);
      }}
      onKeyDown={event=>{
        if(event.key==='Escape'&&expanded){
          event.preventDefault();
          setOpen(false);
          setHighlight(-1);
        }
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          if(!query)return;
          event.preventDefault();
          setOpen(true);
          if(matches.length){
            const next=event.key==='ArrowDown'?highlight+1:highlight<0?matches.length-1:highlight-1;
            setHighlight((next+matches.length)%matches.length);
          }
        }
        if(event.key==='Enter'&&matches[highlight]){
          event.preventDefault();
          select(...matches[highlight]);
        }
      }}/>
    {expanded&&<span className={styles.suggestions}>
      <span id={listId} role="listbox" aria-label="Совпадения">
        {matches.map(([id,label],index)=><span key={id} id={`${listId}-${index}`} role="option"
          aria-selected={highlight===index} className={styles.suggestion}
          onPointerDown={event=>event.preventDefault()} onClick={()=>select(id,label)}>
          {label} <small>· ID {id}</small>
        </span>)}
      </span>
      {!matches.length&&<span className={styles.suggestion} role="status">Совпадений нет</span>}
    </span>}
  </span>;
}
