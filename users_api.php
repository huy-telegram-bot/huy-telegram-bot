<?php
header('Content-Type: application/json; charset=utf-8');
$SECRET='';
if($SECRET && ($_GET['key']??'')!=$SECRET){http_response_code(403);exit('no');}
$a=$_GET['action']??'';$c=preg_replace('/\D/','',$_GET['chat_id']??'');if(!$c)exit('{}');
$f=__DIR__.'/users_'.$c.'.json';
$load=function()use($f){return file_exists($f)?(json_decode(file_get_contents($f),1)?:[]):[];};
$save=function($d)use($f){file_put_contents($f,json_encode($d,256));};
if($a=='get')exit(json_encode(['data'=>$load()]));
if($a=='save'){ $save(json_decode(file_get_contents('php://input'),1)); exit('1'); }
if($a=='update_uid'){ $j=json_decode(file_get_contents('php://input'),1);$d=$load();$d[$j['uid']]=$j['payload'];$save($d);exit('1'); }
if($a=='delete'){ $d=$load();unset($d[$_GET['uid']]);$save($d);exit('1'); }
echo json_encode(['data'=>$load()]);
?>