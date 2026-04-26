<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Validator;
use function Laravel\Prompts\table;

class lists extends Controller
{
    

    public function insert_list(Request $request){
    
        $imageName = $request->symbol.'.'.
        $request->symbol_image->extension();
        $request->symbol_image->move(public_path('uploads'), $imageName);

        $pdfName = $request->name.'.'.
        $request->program_file->extension();
        $request->program_file->move(public_path('uploads'), $pdfName);
        $exist=DB::table('lists')->where('name',$request->name)->exists();
        if($exist){
         return response()->json(["message"=>"list already exists"],400);

        }
        else{
        DB::table("lists")->insert([
            "name"=>$request->name,
            "symbol"=>$request->symbol,
            "symbol_img"=>$imageName,
            "program"=>$pdfName,
            "lists_Dis_id"=>$request->list_Dis_id
            
        ]
        );}
        return response()->json(["message"=>"تم اضافة قائمة جديدة" ]);
        
    }



    public function update_list(Request $request){
       
     $id = $request->id;

    $lists = DB::table('lists')->where('id', $id)->first();
        $data=[];

        if($request->has('name') && $request->name != $lists->name){
            $data['name']=$request->name;
        }

        if($request->has('symbol') && $request->symbol != $lists->symbol){
           $data['symbol']=$request->symbol;
        }


        if ($request->hasFile('symbol_image')) {
        $imageName = $request->symbol.'_img.'.
        $request->symbol_image->extension();
        $request->symbol_image->move(public_path('uploads'), $imageName);
        if ($imageName != $lists->symbol_img){
         $data['symbol_img']=$imageName;
        }
        }

        if ($request->hasFile('program_file')) {
        $pdfName =$request->name.'.'.
        $request->program_file->extension();
        $request->program_file->move(public_path('uploads'), $pdfName);
        if ($pdfName != $lists->program){
         $data['program']=$pdfName;
        }
        }

        DB::table("lists")->where('id',$id)->update($data);

       return response()->json(["message"=>"تم تعديل القائمة"]);
    }

    
    public function delete_list(Request $request){
        $rawIds=$request->ids;
    if (is_string($rawIds)) {
        // شيل الأقواس وأي مسافات، وبعدين قطع النص عند الفاصلة
    $cleanString = trim($rawIds, '[]'); 
    $ids = array_map('trim', explode(',', $cleanString));
    } else {
        $ids = $rawIds;
    }

    // التأكد إن الـ ids بقت أرقام فقط (لضمان إن أول عنصر يتقرأ صح)
    $ids = array_filter($ids, 'is_numeric');
       DB::table("lists")->whereIn('id',$ids)->delete();
       return response()->json(["messade"=>"تم حذف القائمة"]);
      
    }
   


    public function insert_candidates(Request $request){
       
      $user=DB::table('users')->where('full_name',$request->full_name)
             ->where('national_id',$request->national_id)->first(); 
              if(!$user){
                http_response_code(400);
           return response()->json(['message'=>'The entered data is incorrect'],401);  
           }else{
       DB::table("users")->where("id",$user->id)->update(["role"=>"candidate"]);
       DB::table("candidates")->insert([
        "users_id"=>$user->id,
        "lists_id"=>$request->list_id,
        "type"=>"قوائم"

       ]);
       }
       return response()->json(["message"=>"تم اضافة مرشح"]);
       
      
    }
    

    public function delete_candidates(Request $request){
        $rawIds=$request->ids;
    if (is_string($rawIds)) {
        // شيل الأقواس وأي مسافات، وبعدين قطع النص عند الفاصلة
    $cleanString = trim($rawIds, '[]'); 
    $ids = array_map('trim', explode(',', $cleanString));
    } else {
        $ids = $rawIds;
    }

    // التأكد إن الـ ids بقت أرقام فقط (لضمان إن أول عنصر يتقرأ صح)
    $ids = array_filter($ids, 'is_numeric');

    $candidates = DB::table('candidates')->whereIn('id', $ids)->get();

    $userIds = $candidates->pluck('users_id');

    DB::table('users')->whereIn('id', $userIds)->update(['role' => null]);

    DB::table('candidates')->whereIn('id', $ids)->delete();
        return response()->json(["message"=>"تم حدف المرشح"]);

    }
    
    public function store_instruction(Request $request){
      
       
    $election=DB::table('elections')->latest('id')->first();
   
     DB::table('elections')->where('id',$election->id)->update([
      'inst_list'=>$request->instruction

     ]);
   
    return response()->json(['message'=>'save successfully']);

    }

    public function type_elec(Request $request){
        $election=DB::table('elections')->latest('id')->first();
        if(!$election || $election->type != $request->type){
            DB::table("elections")->insert([
                "type"=>$request->type,
                "date_start"=>$election->date_start,
                "date_end"=>$election->date_end,
                "inst_indiv"=>$election->inst_indiv,
                "inst_list"=>$election->inst_list

            ]);
        }
        
        return response()->json(['message'=>'save successfull']);
        
    }
}